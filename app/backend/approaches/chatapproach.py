import json
import re
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Optional

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from approaches.approach import Approach


class ChatApproach(Approach, ABC):
    # Chat roles
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    query_prompt_few_shots = [
        {"role": USER, "content": "Wann greift mein Reiserückrittsschutz?"},
        {"role": ASSISTANT, "content": "Die Nexible Reiserücktrittsversicherung bietet Versicherungsschutz wenn Sie oder eine Ihnen nahestehende Person oder Ihr Reisepartner vor der Reise erkranken und die Reise deshalb nicht antreten können."},
        {"role": USER, "content": "Ist eine professionelle Zahnreinigung in der Zahnzusatzversicherung abgedeckt?"},
        {"role": ASSISTANT, "content": """Eine Reiseversicherung kann verschiedene Leistungen abdecken, die je nach Versicherungspaket und Tarif variieren können. Zu den möglichen Leistungen einer Reiseversicherung gehören:
                        - Reiseabbruchversicherung: Erstattung der Kosten für nicht genutzte Reiseleistungen, wenn die Reise vorzeitig abgebrochen wird.
                        - Reisekrankenversicherung: Übernahme der Kosten für medizinisch notwendige Behandlungen und Operationen im Ausland, einschließlich Schwangerschaft und Zahnbehandlungen.
                        - Reisegepäckversicherung: Erstattung bei Verlust, Diebstahl oder Beschädigung des Reisegepäcks, einschließlich notwendiger Ersatzkäufe.
                        - Reiseunfallversicherung: Auszahlung einer Versicherungssumme bei Unfall oder Invalidität während der Reise.
                        - Reisehaftpflichtversicherung: Schutz vor Schadenersatzansprüchen Dritter während der Reise.
                       Es ist wichtig, die Versicherungsbedingungen und Konditionen der jeweiligen Reiseversicherung zu überprüfen, um herauszufinden, welche Leistungen genau abgedeckt sind."""},
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
    async def run_until_final_call(self, messages, overrides, auth_claims, should_stream) -> tuple:
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

    async def run_without_streaming(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any]:
        extra_info, chat_coroutine = await self.run_until_final_call(
            messages, overrides, auth_claims, should_stream=False
        )
        chat_completion_response: ChatCompletion = await chat_coroutine
        chat_resp = chat_completion_response.model_dump()  # Convert to dict to make it JSON serializable
        chat_resp = chat_resp["choices"][0]
        chat_resp["context"] = extra_info
        if overrides.get("suggest_followup_questions"):
            content, followup_questions = self.extract_followup_questions(chat_resp["message"]["content"])
            chat_resp["message"]["content"] = content
            chat_resp["context"]["followup_questions"] = followup_questions
        chat_resp["session_state"] = session_state
        return chat_resp

    async def run_with_streaming(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ) -> AsyncGenerator[dict, None]:
        extra_info, chat_coroutine = await self.run_until_final_call(
            messages, overrides, auth_claims, should_stream=True
        )
        yield {"delta": {"role": "assistant"}, "context": extra_info, "session_state": session_state}

        followup_questions_started = False
        followup_content = ""
        async for event_chunk in await chat_coroutine:
            # "2023-07-01-preview" API version has a bug where first response has empty choices
            event = event_chunk.model_dump()  # Convert pydantic model to dict
            if event["choices"]:
                completion = {"delta": event["choices"][0]["delta"]}
                # if event contains << and not >>, it is start of follow-up question, truncate
                content = completion["delta"].get("content")
                content = content or ""  # content may either not exist in delta, or explicitly be None
                if overrides.get("suggest_followup_questions") and "<<" in content:
                    followup_questions_started = True
                    earlier_content = content[: content.index("<<")]
                    if earlier_content:
                        completion["delta"]["content"] = earlier_content
                        yield completion
                    followup_content += content[content.index("<<") :]
                elif followup_questions_started:
                    followup_content += content
                else:
                    yield completion
        if followup_content:
            _, followup_questions = self.extract_followup_questions(followup_content)
            yield {"delta": {"role": "assistant"}, "context": {"followup_questions": followup_questions}}

    async def run(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
        context: dict[str, Any] = {},
    ) -> dict[str, Any]:
        overrides = context.get("overrides", {})
        auth_claims = context.get("auth_claims", {})
        return await self.run_without_streaming(messages, overrides, auth_claims, session_state)

    async def run_stream(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
        context: dict[str, Any] = {},
    ) -> AsyncGenerator[dict[str, Any], None]:
        overrides = context.get("overrides", {})
        auth_claims = context.get("auth_claims", {})
        return self.run_with_streaming(messages, overrides, auth_claims, session_state)
