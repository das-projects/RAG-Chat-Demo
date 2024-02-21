import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Optional, Union

from openai.types.chat import (
    ChatCompletion,
    ChatCompletionContentPartParam,
    ChatCompletionMessageParam,
)

from approaches.approach import Approach
from core.messagebuilder import MessageBuilder


class ChatApproach(Approach, ABC):
    # Chat roles
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    query_prompt_few_shots = [
        {'role': USER, 'content': 'Wie können Sie das User Service Center erreichen?'},
        {'role': ASSISTANT,
         'content': 'Sie erreichen das User Service Center von ITERGO telefonisch von Montag bis Freitag zwischen 06:30 und 18:00 Uhr. Außerhalb der Servicezeiten können Sie das User Service Center per E-Mail erreichen. Bitte verwenden Sie das folgende Formular.'},
        {'role': USER, 'content': 'Welche Hinweise sollte ich beachten?'},
        {'role': ASSISTANT,
         'content': 'Bevor Sie das ITERGO User Service Center anrufen, beachten Sie bitte die Checkliste. Sie können auch einen ENEO Systemreport erstellen und diesen unter Angabe der Ticketnummer an die Hotline (benutzerservice@itergo.com) senden.'},
        {'role': USER, 'content': 'Wie erreichen Sie das User Service Center?'},
        {'role': ASSISTANT,
         'content': 'per e-mail benutzerservice@itergo.com Alle Hauptstandorte 2000 Aussendienst 0800 246 2000 Englische Hotline +49 211 477 7444'},
        {'role': USER, 'content': 'Sie sind mit der Lösung des Tickets nicht zufrieden ?'},
        {'role': ASSISTANT, 'content': 'Wenn Sie mit der Lösung Ihres Tickets nicht zufrieden sind, können Sie es innerhalb von 7 Tagen über die Wiedereröffnungsfunktion in MyIT odertelefonisch über das IT User Service Center selbst wiedereröffnen lassen. Sie können uns auch per E-Mail erreichen: Beschwerdemanagement@itergo.com Neu ist, dass Ihr Ticket automatisch mit einer höheren Priorität bearbeitet wird.'},
        {'role': USER, 'content': 'Wo finde ich Tipps und Tricks zur Einrichtung der Hardware?'},
        {'role': ASSISTANT,
         'content': 'Auf der Startseite der IT-Supportseite finden Sie verschiedene Anleitungen zu unterschiedlichen Kategorien. Hier finde Sie auch die  "Anleitung zur Nutzung im mobilen Arbeiten" und "Anleitung zur Nutzung im Büro".'},
        {'role': USER, 'content': 'Wie kann ich über VPN-Verbindung einen Zugriff auf Firmennetz herstellen?'},
        {'role': ASSISTANT,
         'content': 'Auf der Startseite finden Sie ein Erklärvideo  unter  "VPN (Virtuell Private Network) verbinden".'},
        {'role': USER, 'content': 'Warum wird im Homeoffice mein W-Lan nicht gefunden?'},
        {'role': ASSISTANT,
         'content': 'Die Ursachen können Vielfältig sein. Es gibt ein Erklärvideo zu Einrichtung des Internets unter  "Internetverbindung einrichten".'},
   ]

    NO_RESPONSE = "0"

    follow_up_questions_prompt_content = """Generieren Sie drei sehr kurze Folgefragen, die der Benutzer wahrscheinlich als nächstes zu Nexible-Versicherungsprodukten stellen würde. 
    Verwenden Sie doppelte spitze Klammern, um auf die Fragen zu verweisen, z.B. 
    <<Gibt es Ausschlüsse für Rezepte?>>
    <<Wie kann ich meine Kfz Schaden melden?>>
    <<Wann greift mein Reiserückrittsschutz?>>
    Versuchen Sie, bereits gestellte Fragen nicht zu wiederholen.
    Generieren Sie nur Fragen und generieren Sie keinen Text vor oder nach den Fragen, wie z. B. „Nächste Fragen“.
    Stellen Sie sicher, dass die letzte Frage mit ">>" endet."""

    query_prompt_template = """Nachfolgend finden Sie eine Historie der bisherigen Konversation und eine neue Frage des Benutzers, die durch eine Suche in der Wissensdatenbank über Nexible Versicherungsprodukte beantwortet werden muss.
    Generieren Sie eine Suchanfrage basierend auf der Konversation und der neuen Frage. Verwenden Sie die folgenden Regeln: 
    Geben Sie keine zitierten Quelldateinamen und Dokumentnamen wie z. B. info.txt oder doc.pdf in die Suchbegriffe ein.
    Fügen Sie keinen Text innerhalb von [] oder <<>> in die Suchabfragebegriffe ein.
    Fügen Sie keine Sonderzeichen wie '+' ein.
    Wenn die Frage nicht auf Deutsch ist, übersetzen Sie die Frage ins Deutsche, bevor Sie die Suchanfrage generieren.
    Wenn Sie keine Suchabfrage generieren können, geben Sie nur die Zahl 0 zurück.
    """

    @property
    @abstractmethod
    def system_message_chat_conversation(self) -> str:
        pass

    @abstractmethod
    async def run_until_final_call(self, history, overrides, auth_claims, should_stream) -> tuple:
        pass

    def get_system_prompt(self, override_prompt: Optional[str], follow_up_questions_prompt: str) -> str:
        if override_prompt is None:
            return self.system_message_chat_conversation.format(
                injected_prompt="", follow_up_questions_prompt=follow_up_questions_prompt
            )
        elif override_prompt.startswith(">>>"):
            return self.system_message_chat_conversation.format(
                injected_prompt=override_prompt[3:] + "\n", follow_up_questions_prompt=follow_up_questions_prompt
            )
        else:
            return override_prompt.format(follow_up_questions_prompt=follow_up_questions_prompt)

    def get_search_query(self, chat_completion: ChatCompletion, user_query: str):
        response_message = chat_completion.choices[0].message

        if response_message.tool_calls:
            for tool in response_message.tool_calls:
                if tool.type != "function":
                    continue
                function = tool.function
                if function.name == "search_sources":
                    arg = json.loads(function.arguments)
                    search_query = arg.get("search_query", self.NO_RESPONSE)
                    if search_query != self.NO_RESPONSE:
                        return search_query
        elif query_text := response_message.content:
            if query_text.strip() != self.NO_RESPONSE:
                return query_text
        return user_query

    def extract_followup_questions(self, content: str):
        return content.split("<<")[0], re.findall(r"<<([^>>]+)>>", content)

    def get_messages_from_history(
        self,
        system_prompt: str,
        model_id: str,
        history: list[dict[str, str]],
        user_content: Union[str, list[ChatCompletionContentPartParam]],
        max_tokens: int,
        few_shots=[],
    ) -> list[ChatCompletionMessageParam]:
        message_builder = MessageBuilder(system_prompt, model_id)

        # Add examples to show the chat what responses we want. It will try to mimic any responses and make sure they match the rules laid out in the system message.
        for shot in reversed(few_shots):
            message_builder.insert_message(shot.get("role"), shot.get("content"))

        append_index = len(few_shots) + 1

        message_builder.insert_message(self.USER, user_content, index=append_index)

        total_token_count = 0
        for existing_message in message_builder.messages:
            total_token_count += message_builder.count_tokens_for_message(existing_message)

        newest_to_oldest = list(reversed(history[:-1]))
        for message in newest_to_oldest:
            potential_message_count = message_builder.count_tokens_for_message(message)
            if (total_token_count + potential_message_count) > max_tokens:
                logging.info("Reached max tokens of %d, history will be truncated", max_tokens)
                break
            message_builder.insert_message(message["role"], message["content"], index=append_index)
            total_token_count += potential_message_count
        return message_builder.messages

    async def run_without_streaming(
        self,
        history: list[dict[str, str]],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any]:
        extra_info, chat_coroutine = await self.run_until_final_call(
            history, overrides, auth_claims, should_stream=False
        )
        chat_completion_response: ChatCompletion = await chat_coroutine
        chat_resp = chat_completion_response.model_dump()  # Convert to dict to make it JSON serializable
        chat_resp["choices"][0]["context"] = extra_info
        if overrides.get("suggest_followup_questions"):
            content, followup_questions = self.extract_followup_questions(chat_resp["choices"][0]["message"]["content"])
            chat_resp["choices"][0]["message"]["content"] = content
            chat_resp["choices"][0]["context"]["followup_questions"] = followup_questions
        chat_resp["choices"][0]["session_state"] = session_state
        return chat_resp

    async def run_with_streaming(
        self,
        history: list[dict[str, str]],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ) -> AsyncGenerator[dict, None]:
        extra_info, chat_coroutine = await self.run_until_final_call(
            history, overrides, auth_claims, should_stream=True
        )
        yield {
            "choices": [
                {
                    "delta": {"role": self.ASSISTANT},
                    "context": extra_info,
                    "session_state": session_state,
                    "finish_reason": None,
                    "index": 0,
                }
            ],
            "object": "chat.completion.chunk",
        }

        followup_questions_started = False
        followup_content = ""
        async for event_chunk in await chat_coroutine:
            # "2023-07-01-preview" API version has a bug where first response has empty choices
            event = event_chunk.model_dump()  # Convert pydantic model to dict
            if event["choices"]:
                # if event contains << and not >>, it is start of follow-up question, truncate
                content = event["choices"][0]["delta"].get("content")
                content = content or ""  # content may either not exist in delta, or explicitly be None
                if overrides.get("suggest_followup_questions") and "<<" in content:
                    followup_questions_started = True
                    earlier_content = content[: content.index("<<")]
                    if earlier_content:
                        event["choices"][0]["delta"]["content"] = earlier_content
                        yield event
                    followup_content += content[content.index("<<") :]
                elif followup_questions_started:
                    followup_content += content
                else:
                    yield event
        if followup_content:
            _, followup_questions = self.extract_followup_questions(followup_content)
            yield {
                "choices": [
                    {
                        "delta": {"role": self.ASSISTANT},
                        "context": {"followup_questions": followup_questions},
                        "finish_reason": None,
                        "index": 0,
                    }
                ],
                "object": "chat.completion.chunk",
            }

    async def run(
        self, messages: list[dict], stream: bool = False, session_state: Any = None, context: dict[str, Any] = {}
    ) -> Union[dict[str, Any], AsyncGenerator[dict[str, Any], None]]:
        overrides = context.get("overrides", {})
        auth_claims = context.get("auth_claims", {})

        if stream is False:
            return await self.run_without_streaming(messages, overrides, auth_claims, session_state)
        else:
            return self.run_with_streaming(messages, overrides, auth_claims, session_state)
