# Copyright (C) 2024 Prediction Guard, Inc.
# SPDX-License-Identified: Apache-2.0

import os
from typing import Union
from comps.cores.mega.utils import ConfigError, get_access_token, load_model_configs
from fastapi.responses import StreamingResponse
from langchain_core.prompts import PromptTemplate
from .template import ChatTemplate
from openai import OpenAI
from comps import (
    CustomLogger,
    OpeaComponent,
    LLMParamsDoc,
    SearchedDoc,
    ServiceType,
)
from comps.cores.proto.api_protocol import ChatCompletionRequest

logger = CustomLogger("tgi_llm")
logflag = os.getenv("LOGFLAG", False)

# Environment variables
MODEL_NAME = os.getenv("LLM_MODEL_ID")
MODEL_CONFIGS = os.getenv("MODEL_CONFIGS")
DEFAULT_ENDPOINT = os.getenv("TGI_LLM_ENDPOINT", "http://localhost:8080")
TOKEN_URL = os.getenv("TOKEN_URL")
CLIENTID = os.getenv("CLIENTID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

# Validate and Load the models config if MODEL_CONFIGS is not null
configs_map = {}
if MODEL_CONFIGS:
    try:
        configs_map = load_model_configs(MODEL_CONFIGS)
    except ConfigError as e:
        logger.error(f"Failed to load model configurations: {e}")
        raise ConfigError(f"Failed to load model configurations: {e}")

def get_llm_endpoint():
    if not MODEL_CONFIGS:
        return DEFAULT_ENDPOINT
    try:
        return configs_map.get(MODEL_NAME).get("endpoint")
    except ConfigError as e:
        logger.error(f"Input model {model} not present in model_configs. Error {e}")
        raise ConfigError(f"Input model {model} not present in model_configs")


class TGILLM(OpeaComponent):
    """
    A specialized LLM component derived from OpeaComponent for interacting with TGI services.

    Attributes:
        client (TGI): An instance of the TGI client for text generation.
    """

    def __init__(self, name: str, description: str, config: dict = None):
        super().__init__(name, ServiceType.LLM.name.lower(), description, config)
        self.client = self._initialize_client()
    
    def _initialize_client(self) -> OpenAI:
        """Initializes the OpenAI."""
        access_token = (
            get_access_token(TOKEN_URL, CLIENTID, CLIENT_SECRET) if TOKEN_URL and CLIENTID and CLIENT_SECRET else None
        )
        headers = {}
        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
        llm_endpoint = get_llm_endpoint()
        ### TODO add headers
        return OpenAI(
            api_key="EMPTY",
            base_url=llm_endpoint + "/v1",
        )

    def check_health(self) -> bool:
        """
        Checks the health of the TGI LLM service.

        Returns:
            bool: True if the service is reachable and healthy, False otherwise.
        """
        ### TODO fix health problem
        try:
            response = self.client.completions.create(
                model="tgi",
                prompt="How are you?",
                max_tokens=4
            )
            return response is not None
        except Exception as e:
            logger.error(f"Health check failed")
            return False

    def align_input(self, input: Union[LLMParamsDoc, ChatCompletionRequest, SearchedDoc], prompt_template, input_variables):
        if isinstance(input, SearchedDoc):
            if logflag:
                logger.info("[ SearchedDoc ] input from retriever microservice")
            prompt = input.initial_query
            if input.retrieved_docs:
                docs = [doc.text for doc in input.retrieved_docs]
                if logflag:
                    logger.info(f"[ SearchedDoc ] combined retrieved docs: {docs}")
                prompt = ChatTemplate.generate_rag_prompt(input.initial_query, docs, MODEL_NAME)

            ## use default ChatCompletionRequest parameters
            new_input = ChatCompletionRequest(messages=prompt)

            if logflag:
                logger.info(f"[ SearchedDoc ] final input: {new_input}")
            
            return prompt, new_input

        elif isinstance(input, LLMParamsDoc):
            if logflag:
                logger.info("[ LLMParamsDoc ] input from rerank microservice")
            prompt = input.query
            if prompt_template:
                if sorted(input_variables) == ["context", "question"]:
                    prompt = prompt_template.format(question=input.query, context="\n".join(input.documents))
                elif input_variables == ["question"]:
                    prompt = prompt_template.format(question=input.query)
                else:
                    logger.info(
                        f"[ LLMParamsDoc ] {prompt_template} not used, we only support 2 input variables ['question', 'context']"
                    )
            else:
                if input.documents:
                    # use rag default template
                    prompt = ChatTemplate.generate_rag_prompt(input.query, input.documents, input.model)
            
            # convert to unified OpenAI /v1/chat/completions format
            new_input = ChatCompletionRequest(
                messages=prompt,
                max_tokens=input.max_tokens,
                top_p=input.top_p,
                stream=input.streaming,
                frequency_penalty=input.frequency_penalty,
                temperature=input.temperature
            )

            return prompt, new_input

        else:
            if logflag:
                logger.info("[ ChatCompletionRequest ] input in opea format")
            
            prompt = input.messages
            if prompt_template:
                if sorted(input_variables) == ["context", "question"]:
                    prompt = prompt_template.format(question=input.messages, context="\n".join(input.documents))
                elif input_variables == ["question"]:
                    prompt = prompt_template.format(question=input.messages)
                else:
                    logger.info(
                        f"[ ChatCompletionRequest ] {prompt_template} not used, we only support 2 input variables ['question', 'context']"
                    )
            else:
                if input.documents:
                    # use rag default template
                    prompt = ChatTemplate.generate_rag_prompt(input.messages, input.documents, input.model)
            
            return prompt, input

    def invoke(self, input: Union[LLMParamsDoc, ChatCompletionRequest, SearchedDoc]):
        """
        Invokes the TGI LLM service to generate output for the provided input.

        Args:
            input (Union[LLMParamsDoc, ChatCompletionRequest, SearchedDoc]): The input text(s).
        """

        prompt_template = None
        input_variables = None
        if not isinstance(input, SearchedDoc) and input.chat_template:
            prompt_template = PromptTemplate.from_template(input.chat_template)
            input_variables = prompt_template.input_variables

        if isinstance(input, ChatCompletionRequest) and not isinstance(input.messages, str):
            if logflag:
                logger.info("[ ChatCompletionRequest ] input in opea format")

            if input.messages[0]["role"] == "system":
                if "{context}" in input.messages[0]["content"]:
                    if input.documents is None or input.documents == []:
                        input.messages[0]["content"].format(context="")
                    else:
                        input.messages[0]["content"].format(context="\n".join(input.documents))
            else:
                if prompt_template:
                    system_prompt = prompt_template
                    if input_variables == ["context"]:
                        system_prompt = prompt_template.format(context="\n".join(input.documents))
                    else:
                        logger.info(
                            f"[ ChatCompletionRequest ] {prompt_template} not used, only support 1 input variables ['context']"
                        )

                    input.messages.insert(0, {"role": "system", "content": system_prompt})

            chat_completion = self.client.chat.completions.create(
                model="tgi",
                messages=input.messages,
                frequency_penalty=input.frequency_penalty,
                logit_bias=input.logit_bias,
                logprobs=input.logprobs,
                top_logprobs=input.top_logprobs,
                max_tokens=input.max_tokens,
                n=input.n,
                presence_penalty=input.presence_penalty,
                response_format=input.response_format,
                seed=input.seed,
                service_tier=input.service_tier,
                stop=input.stop,
                stream=input.stream,
                stream_options=input.stream_options,
                temperature=input.temperature,
                top_p=input.top_p,
                tools=input.tools,
                tool_choice=input.tool_choice,
                parallel_tool_calls=input.parallel_tool_calls,
                user=input.user,
            )
        else:
            prompt, input = self.align_input(input, prompt_template, input_variables)

            chat_completion = self.client.completions.create(
                model="tgi",
                prompt=prompt,
                best_of=input.best_of,
                echo=input.echo,
                frequency_penalty=input.frequency_penalty,
                logit_bias=input.logit_bias,
                logprobs=input.logprobs,
                max_tokens=input.max_tokens,
                n=input.n,
                presence_penalty=input.presence_penalty,
                seed=input.seed,
                stop=input.stop,
                stream=input.stream,
                suffix=input.suffix,
                temperature=input.temperature,
                top_p=input.top_p,
                user=input.user,
            )

        if input.stream:

            def stream_generator():
                for c in chat_completion:
                    if logflag:
                        logger.info(c)
                    chunk = c.model_dump_json()
                    if chunk not in ["<|im_end|>", "<|endoftext|>"]:
                        yield f"data: {chunk}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            if logflag:
                logger.info(chat_completion)
            return chat_completion
        