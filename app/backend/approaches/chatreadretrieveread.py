from typing import Any, Coroutine, Literal, Optional, Union, overload

from approaches.approach import ThoughtStep
from approaches.chatapproach import ChatApproach
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorQuery
from core.authentication import AuthenticationHelper
from core.modelhelper import get_token_limit
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
)


class ChatReadRetrieveReadApproach(ChatApproach):
    """
    A multi-step approach that first uses OpenAI to turn the user's question into a search query,
    then uses Azure AI Search to retrieve relevant documents, and then sends the conversation history,
    original user question, and search results to OpenAI to generate a response.
    """

    def __init__(
            self,
            *,
            search_client: SearchClient,
            auth_helper: AuthenticationHelper,
            openai_client: AsyncOpenAI,
            chatgpt_model: str,
            chatgpt_deployment: Optional[str],  # Not needed for non-Azure OpenAI
            embedding_deployment: Optional[str],  # Not needed for non-Azure OpenAI or for retrieval_mode="text"
            embedding_model: str,
            sourcepage_field: str,
            content_field: str,
            query_language: str,
            query_speller: str,
    ):
        self.search_client = search_client
        self.openai_client = openai_client
        self.auth_helper = auth_helper
        self.chatgpt_model = chatgpt_model
        self.chatgpt_deployment = chatgpt_deployment
        self.embedding_deployment = embedding_deployment
        self.embedding_model = embedding_model
        self.sourcepage_field = sourcepage_field
        self.content_field = content_field
        self.query_language = query_language
        self.query_speller = query_speller
        self.chatgpt_token_limit = get_token_limit(chatgpt_model)

    @property
    def system_message_chat_conversation(self):
        return """Sie sind ein Nexible-Kundendienstassistent, der Nexible-Kunden bei Fragen zu Reiseversicherungen und Zahnzusatzversicherungen von Nexible hilft.
        Bitte denken Sie darüber nach, ob die Frage des Nutzers unklar formuliert oder mehrdeutig ist, und bitten Sie den Nutzer, sie zu erläutern oder anders zu formulieren. 
        Bitte geben Sie eine umfassende Antwort NUR mit den Fakten, nach sorgfältiger Prüfung der Liste der Quellen unten aufgeführt sind. Bitte halten Sie Ihre Antworten so kurz wie möglich.
        Wenn Sie nicht sicher sind, ob die Antwort aus dem bereitgestellten Zitat stammt, geben Sie die Antwort nicht an. Generieren Sie keine Antworten, die nicht die folgenden Quellen verwenden. Wenn die Informationen unten nicht ausreichen, sagen Sie, dass Sie es nicht wissen, und bitten Sie um Kontaktaufnahme unter https://www.nexible.de/kontakt.
        Für tabellarische Informationen geben Sie sie als HTML-Tabelle zurück. Geben Sie das Markdown-Format nicht zurück. 
        Wenn die Frage nicht auf Deutsch ist, antworten Sie in der Sprache, die in der Frage verwendet wird.
        Jede Quelle hat einen Namen, gefolgt von einem Doppelpunkt und der eigentlichen Information. Nenne bitte jederzeit die Quelle, die zur Generierung der Antwort verwendet wurde. Nutze dafür eckige Klammern, z.B. [broschuere.pdf]. 
        Kombiniere niemals mehrere Quellen und zitiere Quellen immer separat , z.B. [zahn_broschuere.pdf][kfz_broschuere.pdf].
        {follow_up_questions_prompt}
        {injected_prompt}
        """

    @overload
    async def run_until_final_call(
            self,
            history: list[dict[str, str]],
            overrides: dict[str, Any],
            auth_claims: dict[str, Any],
            should_stream: Literal[False],
    ) -> tuple[dict[str, Any], Coroutine[Any, Any, ChatCompletion]]:
        ...

    @overload
    async def run_until_final_call(
            self,
            history: list[dict[str, str]],
            overrides: dict[str, Any],
            auth_claims: dict[str, Any],
            should_stream: Literal[True],
    ) -> tuple[dict[str, Any], Coroutine[Any, Any, AsyncStream[ChatCompletionChunk]]]:
        ...

    async def run_until_final_call(
            self,
            history: list[dict[str, str]],
            overrides: dict[str, Any],
            auth_claims: dict[str, Any],
            should_stream: bool = False,
    ) -> tuple[dict[str, Any], Coroutine[Any, Any, Union[ChatCompletion, AsyncStream[ChatCompletionChunk]]]]:
        has_text = overrides.get("retrieval_mode") in ["text", "hybrid", None]
        has_vector = overrides.get("retrieval_mode") in ["vectors", "hybrid", None]
        use_semantic_captions = True if overrides.get("semantic_captions") and has_text else False
        top = overrides.get("top", 3)
        filter = self.build_filter(overrides, auth_claims)
        use_semantic_ranker = True if overrides.get("semantic_ranker") and has_text else False

        original_user_query = history[-1]["content"]
        user_query_request = "Generate search query for: " + original_user_query

        functions = [
            {
                "name": "search_sources",
                "description": "Retrieve sources from the Azure AI Search index",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "search_query": {
                            "type": "string",
                            "description": "Query string to retrieve documents from azure search eg: 'Health care plan'",
                        }
                    },
                    "required": ["search_query"],
                },
            }
        ]

        # STEP 1: Generate an optimized keyword search query based on the chat history and the last question
        messages = self.get_messages_from_history(
            system_prompt=self.query_prompt_template,
            model_id=self.chatgpt_model,
            history=history,
            user_content=user_query_request,
            max_tokens=self.chatgpt_token_limit - len(user_query_request),
            few_shots=self.query_prompt_few_shots,
        )

        chat_completion: ChatCompletion = await self.openai_client.chat.completions.create(
            messages=messages,  # type: ignore
            # Azure Open AI takes the deployment name as the model name
            model=self.chatgpt_deployment if self.chatgpt_deployment else self.chatgpt_model,
            temperature=0.0,
            max_tokens=100,  # Setting too low risks malformed JSON, setting too high may affect performance
            n=1,
            functions=functions,
            function_call="auto",
        )

        query_text = self.get_search_query(chat_completion, original_user_query)

        # STEP 2: Retrieve relevant documents from the search index with the GPT optimized query

        # If retrieval mode includes vectors, compute an embedding for the query
        vectors: list[VectorQuery] = []
        if has_vector:
            vectors.append(await self.compute_text_embedding(query_text))

        # Only keep the text query if the retrieval mode uses text, otherwise drop it
        if not has_text:
            query_text = None

        results = await self.search(top, query_text, filter, vectors, use_semantic_ranker, use_semantic_captions)

        sources_content = self.get_sources_content(results, use_semantic_captions, use_image_citation=False)
        content = "\n".join(sources_content)

        # STEP 3: Generate a contextual and content specific answer using the search results and chat history

        # Allow client to replace the entire prompt, or to inject into the exiting prompt using >>>
        system_message = self.get_system_prompt(
            overrides.get("prompt_template"),
            self.follow_up_questions_prompt_content if overrides.get("suggest_followup_questions") else "",
        )

        response_token_limit = 1024
        messages_token_limit = self.chatgpt_token_limit - response_token_limit
        messages = self.get_messages_from_history(
            system_prompt=system_message,
            model_id=self.chatgpt_model,
            history=history,
            # Model does not handle lengthy system messages well. Moving sources to latest user conversation to solve follow up questions prompt.
            user_content=original_user_query + "\n\nSources:\n" + content,
            max_tokens=messages_token_limit,
            few_shots=self.query_prompt_few_shots,
        )

        data_points = {"text": sources_content}

        extra_info = {
            "data_points": data_points,
            "thoughts": [
                ThoughtStep(
                    "Original user query",
                    original_user_query,
                ),
                ThoughtStep(
                    "Generated search query",
                    query_text,
                    {"use_semantic_captions": use_semantic_captions, "has_vector": has_vector},
                ),
                ThoughtStep("Results", [result.serialize_for_results() for result in results]),
                ThoughtStep("Prompt", [str(message) for message in messages]),
            ],
        }

        chat_coroutine = self.openai_client.chat.completions.create(
            # Azure Open AI takes the deployment name as the model name
            model=self.chatgpt_deployment if self.chatgpt_deployment else self.chatgpt_model,
            messages=messages,
            temperature=overrides.get("temperature") or 0.7,
            max_tokens=response_token_limit,
            n=1,
            stream=should_stream,
        )
        return (extra_info, chat_coroutine)
