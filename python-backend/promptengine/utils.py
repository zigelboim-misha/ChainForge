from typing import Dict, Tuple, List, Union, Callable
import json, os, time, asyncio

from promptengine.models import LLM

DALAI_MODEL = None
DALAI_RESPONSE = None

async def call_chatgpt(prompt: str, model: LLM, n: int = 1, temperature: float = 1.0, system_msg: Union[str, None]=None) -> Tuple[Dict, Dict]:
    """
        Calls GPT3.5 via OpenAI's API. 
        Returns raw query and response JSON dicts. 

        NOTE: It is recommended to set an environment variable OPENAI_API_KEY with your OpenAI API key
    """
    import openai
    if not openai.api_key:
        openai.api_key = os.environ.get("OPENAI_API_KEY")
    model = model.value
    print(f"Querying OpenAI model '{model}' with prompt '{prompt}'...")
    system_msg = "You are a helpful assistant." if system_msg is None else system_msg
    query = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "n": n,
        "temperature": temperature,
    }
    response = openai.ChatCompletion.create(**query)
    return query, response

async def call_anthropic(prompt: str, model: LLM, n: int = 1, temperature: float= 1.0,
                        custom_prompt_wrapper: Union[Callable[[str], str], None]=None,
                        max_tokens_to_sample=1024,
                        stop_sequences: Union[List[str], str]=["\n\nHuman:"],
                        async_mode=False,
                        **params) -> Tuple[Dict, Dict]:
    """
        Calls Anthropic API with the given model, passing in params.
        Returns raw query and response JSON dicts.

        Unique parameters:
            - custom_prompt_wrapper: Anthropic models expect prompts in form "\n\nHuman: ${prompt}\n\nAssistant". If you wish to 
                                     explore custom prompt wrappers that deviate, write a function that maps from 'prompt' to custom wrapper.
                                     If set to None, defaults to Anthropic's suggested prompt wrapper.
            - max_tokens_to_sample: A maximum number of tokens to generate before stopping.
            - stop_sequences: A list of strings upon which to stop generating. Defaults to ["\n\nHuman:"], the cue for the next turn in the dialog agent.
            - async_mode: Evaluation access to Claude limits calls to 1 at a time, meaning we can't take advantage of async.
                          If you want to send all 'n' requests at once, you can set async_mode to True.

        NOTE: It is recommended to set an environment variable ANTHROPIC_API_KEY with your Anthropic API key
    """
    import anthropic
    client = anthropic.Client(os.environ["ANTHROPIC_API_KEY"])

    # Format query
    query = {
        'model': model.value,
        'prompt': f"{anthropic.HUMAN_PROMPT} {prompt}{anthropic.AI_PROMPT}" if not custom_prompt_wrapper else custom_prompt_wrapper(prompt),
        'max_tokens_to_sample': max_tokens_to_sample,
        'stop_sequences': stop_sequences,
        'temperature': temperature,
        **params
    }

    print(f"Calling Anthropic model '{model.value}' with prompt '{prompt}' (n={n}). Please be patient...")

    # Request responses using the passed async_mode
    responses = []
    if async_mode:
        # Gather n responses by firing off all API requests at once 
        tasks = [client.acompletion(**query) for _ in range(n)]
        responses = await asyncio.gather(*tasks)
    else:
        # Repeat call n times, waiting for each response to come in:
        while len(responses) < n:
            resp = await client.acompletion(**query)
            responses.append(resp)
            print(f'{model.value} response {len(responses)} of {n}:\n', resp)

    return query, responses

async def call_dalai(model: LLM, port: int, prompt: str, n: int = 1, temperature: float = 0.5, **params) -> Tuple[Dict, Dict]:
    """
        Calls a Dalai server running LLMs Alpaca, Llama, etc locally.
        Returns the raw query and response JSON dicts. 

        Parameters:
            - model: The LLM model, whose value is the name known byt Dalai; e.g. 'alpaca.7b'
            - port: The port of the local server where Dalai is running. Usually 3000.
            - prompt: The prompt to pass to the LLM.
            - n: How many times to query. If n > 1, this will continue to query the LLM 'n' times and collect all responses.
            - temperature: The temperature to query at
            - params: Any other Dalai-specific params to pass. For more info, see https://cocktailpeanut.github.io/dalai/#/?id=syntax-1 

        TODO: Currently, this uses a modified dalaipy library for simplicity; however, in the future we might remove this dependency. 
    """
    # Import and load upon first run
    global DALAI_MODEL, DALAI_RESPONSE
    server = 'http://localhost:'+str(port)
    if DALAI_MODEL is None:
        from promptengine.dalaipy import Dalai
        DALAI_MODEL = Dalai(server)
    elif DALAI_MODEL.server != server:  # if the port has changed, we need to create a new model
        DALAI_MODEL = Dalai(server)
    
    # Make sure server is connected
    DALAI_MODEL.connect()

    # Create settings dict to pass to Dalai as args
    def_params = {'n_predict':128, 'repeat_last_n':64, 'repeat_penalty':1.3, 'seed':-1, 'threads':4, 'top_k':40, 'top_p':0.9}
    for key in params:
        if key in def_params:
            def_params[key] = params[key]
        else:
            print(f"Attempted to pass unsupported param '{key}' to Dalai. Ignoring.")
    
    # Create full query to Dalai
    query = {
        'prompt': prompt,
        'model': model.value,
        'id': str(round(time.time()*1000)),
        'temp': temperature,
        **def_params
    }

    # Create spot to put response and a callback that sets it
    DALAI_RESPONSE = None
    def on_finish(r):
        global DALAI_RESPONSE
        DALAI_RESPONSE = r
    
    print(f"Calling Dalai model '{query['model']}' with prompt '{query['prompt']}' (n={n}). Please be patient...")

    # Repeat call n times
    responses = []
    while len(responses) < n:

        # Call the Dalai model 
        req = DALAI_MODEL.generate_request(**query)
        sent_req_success = DALAI_MODEL.generate(req, on_finish=on_finish)

        if not sent_req_success:
            print("Something went wrong pinging the Dalai server. Returning None.")
            return None, None

        # Blocking --wait for request to complete: 
        while DALAI_RESPONSE is None:
            await asyncio.sleep(0.01)

        response = DALAI_RESPONSE['response']
        if response[-5:] == '<end>':  # strip ending <end> tag, if present
            response = response[:-5]
        if response.index('\r\n') > -1:  # strip off the prompt, which is included in the result up to \r\n:
            response = response[(response.index('\r\n')+2):]
        DALAI_RESPONSE = None

        responses.append(response)
        print(f'Response {len(responses)} of {n}:\n', response)

    # Disconnect from the server
    DALAI_MODEL.disconnect()

    return query, responses

def _extract_chatgpt_responses(response: dict) -> List[dict]:
    """
        Extracts the text part of a response JSON from ChatGPT. If there are more
        than 1 response (e.g., asking the LLM to generate multiple responses), 
        this produces a list of all returned responses.
    """
    choices = response["response"]["choices"]
    return [
        c["message"]["content"]
        for c in choices
    ]

def extract_responses(response: Union[list, dict], llm: Union[LLM, str]) -> List[dict]:
    """
        Given a LLM and a response object from its API, extract the
        text response(s) part of the response object.
    """
    if llm is LLM.ChatGPT or llm == LLM.ChatGPT.value or llm is LLM.GPT4 or llm == LLM.GPT4.value:
        return _extract_chatgpt_responses(response)
    elif llm is LLM.Alpaca7B or llm == LLM.Alpaca7B.value:
        return response["response"]
    elif (isinstance(llm, LLM) and llm.value[:6] == 'claude') or (isinstance(llm, str) and llm[:6] == 'claude'):
        return [r["completion"] for r in response["response"]]
    else:
        raise ValueError(f"LLM {llm} is unsupported.")

def cull_responses(response: Union[list, dict], llm: Union[LLM, str], n: int) -> Union[list, dict]:
    """
        Returns the same 'response' but with only 'n' responses.
    """
    if llm is LLM.ChatGPT or llm == LLM.ChatGPT.value or llm is LLM.GPT4 or llm == LLM.GPT4.value:
        response["response"]["choices"] = response["response"]["choices"][:n]
        return response
    elif llm is LLM.Alpaca7B or llm == LLM.Alpaca7B.value:
        response["response"] = response["response"][:n]
        return response
    elif (isinstance(llm, LLM) and llm.value[:6] == 'claude') or (isinstance(llm, str) and llm[:6] == 'claude'):
        response["response"] = response["response"][:n]
        return response
    else:
        raise ValueError(f"LLM {llm} is unsupported.")

def create_dir_if_not_exists(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path)

def is_valid_filepath(filepath: str) -> bool:
    try:
        with open(filepath, 'r'):
            pass
    except IOError:
        try:
            # Create the file if it doesn't exist, and write an empty json string to it
            with open(filepath, 'w+') as f:
                f.write("{}")
                pass
        except IOError:
            return False
    return True

def is_valid_json(json_dict: dict) -> bool:
    if isinstance(json_dict, dict):
        try:
            json.dumps(json_dict)
            return True
        except:
            pass
    return False

def get_files_at_dir(path: str) -> list:
    f = []
    for (dirpath, dirnames, filenames) in os.walk(path):
        f = filenames
        break
    return f