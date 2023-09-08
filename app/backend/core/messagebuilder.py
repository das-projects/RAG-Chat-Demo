from .modelhelper import num_tokens_from_messages


class MessageBuilder:
    """
      A class for building and managing messages in a chat conversation.
      Attributes:
          messages (list): A list of dictionaries representing chat messages.
          model (str): The name of the ChatGPT model.
          token_length (int): The total number of tokens in the conversation.
          max_tokens (int): The maximum number of tokens allowed in the conversation.
      Methods:
          __init__(self, system_content: str, chatgpt_model: str): Initializes the MessageBuilder instance.
          append_message(self, role: str, content: str, index: int = 1): Appends a new message to the conversation.
      """

    def __init__(self, system_content: str, chatgpt_model: str, max_tokens: int):
        self.messages = [{'role': 'system', 'content': system_content}]
        self.model = chatgpt_model
        self.token_length = num_tokens_from_messages(
            self.messages[-1], self.model)
        self.max_tokens = max_tokens

    def append_message(self, role: str, content: str, index: int = 1):
        self.messages.insert(index, {'role': role, 'content': content})
        new_token_length = self.token_length + num_tokens_from_messages(
            self.messages[index], 
            self.model
            )
        if new_token_length > self.max_tokens:
            self.messages.pop()
        else:
            self.token_length = new_token_length

