import json
from typing import Any, AsyncGenerator

import openai
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType

from approaches.approach import Approach
from core.messagebuilder import MessageBuilder
from core.modelhelper import get_token_limit
from text import nonewlines


class ChatReadRetrieveReadApproach(Approach):
    # Chat roles
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    NO_RESPONSE = "0"

    """
    Simple retrieve-then-read implementation, using the Cognitive Search and OpenAI APIs directly. It first retrieves
    top documents from search, then constructs a prompt with them, and then uses OpenAI to generate an completion
    (answer) with that prompt.
    """
    system_message_chat_conversation = """Sie sind ein Nexible-Kundendienstassistent, der Nexible-Kunden bei Fragen zu Reiseversicherungen und Zahnzusatzversicherungen von Nexible hilft.
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
    follow_up_questions_prompt_content = """Generieren Sie drei sehr kurze Folgefragen, die der Benutzer wahrscheinlich als nächstes zu Nexible-Versicherungsprodukten stellen würde. 
Verwenden Sie doppelte spitze Klammern, um auf die Fragen zu verweisen, z.B. <<Gibt es Ausschlüsse für Rezepte?>>. 
Versuchen Sie, bereits gestellte Fragen nicht zu wiederholen.
Generieren Sie nur Fragen und generieren Sie keinen Text vor oder nach den Fragen, wie z. B. „Nächste Fragen“."""

    query_prompt_template = """Nachfolgend finden Sie eine Historie der bisherigen Konversation und eine neue Frage des Benutzers, die durch eine Suche in der Wissensdatenbank über Nexible Versicherungsprodukte beantwortet werden muss.
Generieren Sie eine Suchanfrage basierend auf der Konversation und der neuen Frage. Verwenden Sie die folgenden Regeln: 
Geben Sie keine zitierten Quelldateinamen und Dokumentnamen wie z. B. info.txt oder doc.pdf in die Suchbegriffe ein.
Fügen Sie keinen Text innerhalb von [] oder <<>> in die Suchabfragebegriffe ein.
Fügen Sie keine Sonderzeichen wie '+' ein.
Wenn die Frage nicht auf Deutsch ist, übersetzen Sie die Frage ins Deutsche, bevor Sie die Suchanfrage generieren.
Wenn Sie keine Suchabfrage generieren können, geben Sie nur die Zahl 0 zurück.
"""
    query_prompt_few_shots = [
        {'role' : USER, 'content' : 'Wann greift mein Reiserückrittsschutz?' },
        {'role' : ASSISTANT, 'content' : 'Die Nexible Reiserücktrittsversicherung bietet Versicherungsschutz wenn Sie oder eine Ihnen nahestehende Person oder Ihr Reisepartner vor der Reise erkranken und die Reise deshalb nicht antreten können. ' },
        {'role' : USER, 'content' : 'Ist eine professionelle Zahnreinigung in der Zahnzusatzversicherung abgedeckt?' },
        {'role' : ASSISTANT, 'content' : 'Das hängt von ihren Tarif ab. Im Basic Tarif sind 60€ pro Jahr abgedeckt, in allen anderen Tarifen 100%.'},
        {'role' : USER, 'content' : 'Kann ich bei nexible eine Hausatversicherung abschließen?' },
        {'role' : ASSISTANT, 'content' : 'Nein, nexible bietet aber umfangreiche Produkte der Zahnzusatzversicherung und Reiseversicherung an.' },
        {'role' : USER, 'content' : 'Wie kann ich einen Schaden melden?' },
        {'role' : ASSISTANT, 'content' : 'Zu welchem Produkt möchten Sie einen Schaden melden?' },
        {'role' : USER, 'content' : 'Zu meiner Reiserücktrittsversicherung' },
        {'role' : ASSISTANT, 'content' : 'Ihren Schadenfalls können Sie ganz einfach online melden unter: https://www.nexible.de/schaden/reiseversicherung' },
        {'role' : USER, 'content' : 'Wie kann ich meine Reiseversicherung abschließen oder berechnen?' },
        {'role' : ASSISTANT, 'content' : 'Sie können Ihre Reiseversicherung ganz einfach online abschließen unter:  https://www.nexible.de/reiseversicherung/online-berechnen/anzahl_versicherter_personen' },
        {'role' : USER, 'content' : 'Wie kann ich meine Kfz Schaden melden?' },
        {'role' : ASSISTANT, 'content' : 'Ihren Schadenfalls können Sie ganz einfach online melden unter: https://www.nexible.de/schaden/autoversicherung/schadenmeldung' },
        {'role' : USER, 'content' : 'Wie kann ich meine Schaden in der Reiseversicherung melden will?' },
        {'role' : ASSISTANT, 'content' : 'Ihren Schadenfalls können Sie ganz einfach online melden unter: https://www.nexible.de/schaden/reiseversicherung' },
        {'role' : USER, 'content' : 'Wie kann ich meine Leistungsfall in der Zahnversicherung geltend machen?' },
        {'role' : ASSISTANT, 'content' : 'Ihren Leistungsfall können Sie ganz einfach online melden unter: https://www.nexible.de/kontakt' },
    ]

    def __init__(
        self,
        search_client: SearchClient,
        openai_host: str,
        chatgpt_deployment: str,
        chatgpt_model: str,
        embedding_deployment: str,
        embedding_model: str,
        sourcepage_field: str,
        content_field: str,
    ):
        self.search_client = search_client
        self.openai_host = openai_host
        self.chatgpt_deployment = chatgpt_deployment
        self.chatgpt_model = chatgpt_model
        self.embedding_deployment = embedding_deployment
        self.embedding_model = embedding_model
        self.sourcepage_field = sourcepage_field
        self.content_field = content_field
        self.chatgpt_token_limit = get_token_limit(chatgpt_model)

    async def run_until_final_call(
        self,
        history: list[dict[str, str]],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        should_stream: bool = False,
    ) -> tuple:
        has_text = overrides.get("retrieval_mode") in ["text", "hybrid", None]
        has_vector = overrides.get("retrieval_mode") in ["vectors", "hybrid", None]
        use_semantic_captions = True if overrides.get("semantic_captions") and has_text else False
        top = overrides.get("top", 3)
        filter = self.build_filter(overrides, auth_claims)

        user_query_request = "Generate search query for: " + history[-1]["user"]

        functions = [
            {
                "name": "search_sources",
                "description": "Retrieve sources from the Azure Cognitive Search index",
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
            self.query_prompt_template,
            self.chatgpt_model,
            history,
            user_query_request,
            self.query_prompt_few_shots,
            self.chatgpt_token_limit - len(user_query_request),
        )

        chatgpt_args = {"deployment_id": self.chatgpt_deployment} if self.openai_host == "azure" else {}
        chat_completion = await openai.ChatCompletion.acreate(
            **chatgpt_args,
            model=self.chatgpt_model,
            messages=messages,
            temperature=0.0,
            max_tokens=32,
            n=1,
            functions=functions,
            function_call="auto",
        )

        query_text = self.get_search_query(chat_completion, history[-1]["user"])

        # STEP 2: Retrieve relevant documents from the search index with the GPT optimized query

        # If retrieval mode includes vectors, compute an embedding for the query
        if has_vector:
            embedding_args = {"deployment_id": self.embedding_deployment} if self.openai_host == "azure" else {}
            embedding = await openai.Embedding.acreate(**embedding_args, model=self.embedding_model, input=query_text)
            query_vector = embedding["data"][0]["embedding"]
        else:
            query_vector = None

        # Only keep the text query if the retrieval mode uses text, otherwise drop it
        if not has_text:
            query_text = None

        # Use semantic L2 re-ranker if requested and if retrieval mode is text or hybrid (vectors + text)
        if overrides.get("semantic_ranker") and has_text:
            r = await self.search_client.search(
                query_text,
                filter=filter,
                query_type=QueryType.SEMANTIC,
                query_language="de-de",
                query_speller="lexicon",
                semantic_configuration_name="default",
                top=top,
                query_caption="extractive|highlight-false" if use_semantic_captions else None,
                vector=query_vector,
                top_k=50 if query_vector else None,
                vector_fields="embedding" if query_vector else None,
            )
        else:
            r = await self.search_client.search(
                query_text,
                filter=filter,
                top=top,
                vector=query_vector,
                top_k=50 if query_vector else None,
                vector_fields="embedding" if query_vector else None,
            )
        if use_semantic_captions:
            results = [
                doc[self.sourcepage_field] + ": " + nonewlines(" . ".join([c.text for c in doc["@search.captions"]]))
                async for doc in r
            ]
        else:
            results = [doc[self.sourcepage_field] + ": " + nonewlines(doc[self.content_field]) async for doc in r]
        content = "\n".join(results)

        follow_up_questions_prompt = (
            self.follow_up_questions_prompt_content if overrides.get("suggest_followup_questions") else ""
        )

        # STEP 3: Generate a contextual and content specific answer using the search results and chat history

        # Allow client to replace the entire prompt, or to inject into the exiting prompt using >>>
        prompt_override = overrides.get("prompt_template")
        if prompt_override is None:
            system_message = self.system_message_chat_conversation.format(
                injected_prompt="", follow_up_questions_prompt=follow_up_questions_prompt
            )
        elif prompt_override.startswith(">>>"):
            system_message = self.system_message_chat_conversation.format(
                injected_prompt=prompt_override[3:] + "\n", follow_up_questions_prompt=follow_up_questions_prompt
            )
        else:
            system_message = prompt_override.format(follow_up_questions_prompt=follow_up_questions_prompt)

        messages = self.get_messages_from_history(
            system_message,
            self.chatgpt_model,
            history,
            history[-1]["user"] + "\n\nSources:\n" + content,
            max_tokens=self.chatgpt_token_limit,  # Model does not handle lengthy system messages well. Moving sources to latest user conversation to solve follow up questions prompt.
        )
        msg_to_display = "\n\n".join([str(message) for message in messages])

        extra_info = {
            "data_points": results,
            "thoughts": f"Searched for:<br>{query_text}<br><br>Conversations:<br>"
            + msg_to_display.replace("\n", "<br>"),
        }

        chat_coroutine = openai.ChatCompletion.acreate(
            **chatgpt_args,
            model=self.chatgpt_model,
            messages=messages,
            temperature=overrides.get("temperature") or 0.7,
            max_tokens=1024,
            n=1,
            stream=should_stream,
        )
        return (extra_info, chat_coroutine)

    async def run_without_streaming(
        self, history: list[dict[str, str]], overrides: dict[str, Any], auth_claims: dict[str, Any]
    ) -> dict[str, Any]:
        extra_info, chat_coroutine = await self.run_until_final_call(
            history, overrides, auth_claims, should_stream=False
        )
        chat_resp = await chat_coroutine
        chat_content = chat_resp.choices[0].message.content
        extra_info["answer"] = chat_content
        return extra_info

    async def run_with_streaming(
        self, history: list[dict[str, str]], overrides: dict[str, Any], auth_claims: dict[str, Any]
    ) -> AsyncGenerator[dict, None]:
        extra_info, chat_coroutine = await self.run_until_final_call(
            history, overrides, auth_claims, should_stream=True
        )
        yield extra_info
        async for event in await chat_coroutine:
            # "2023-07-01-preview" API version has a bug where first response has empty choices
            if event["choices"]:
                yield event

    def get_messages_from_history(
        self,
        system_prompt: str,
        model_id: str,
        history: list[dict[str, str]],
        user_conv: str,
        few_shots=[],
        max_tokens: int = 4096,
    ) -> list:
        message_builder = MessageBuilder(system_prompt, model_id, max_tokens=max_tokens)

        # Add examples to show the chat what responses we want. It will try to mimic any responses and make sure they match the rules laid out in the system message.
        for shot in few_shots:
            message_builder.append_message(shot.get("role"), shot.get("content"))

        user_content = user_conv
        append_index = len(few_shots) + 1

        message_builder.append_message(self.USER, user_content, index=append_index)

        for h in reversed(history[:-1]):
            if bot_msg := h.get("bot"):
                message_builder.append_message(self.ASSISTANT, bot_msg, index=append_index)
            if user_msg := h.get("user"):
                message_builder.append_message(self.USER, user_msg, index=append_index)
            if message_builder.token_length > max_tokens:
                break

        messages = message_builder.messages
        return messages

    def get_search_query(self, chat_completion: dict[str, any], user_query: str):
        response_message = chat_completion["choices"][0]["message"]
        if function_call := response_message.get("function_call"):
            if function_call["name"] == "search_sources":
                arg = json.loads(function_call["arguments"])
                search_query = arg.get("search_query", self.NO_RESPONSE)
                if search_query != self.NO_RESPONSE:
                    return search_query
        elif query_text := response_message.get("content"):
            if query_text.strip() != self.NO_RESPONSE:
                return query_text
        return user_query
