"""
Core AI Service.

Handles chat history (Redis) and OpenAI API interaction with strict user isolation.
"""

import json
from typing import List, Dict, Any, Optional
from openai import OpenAI
import os

from core.config import get_settings
from cache.redis_client import cache_get, cache_set, make_key
from core.constants import RedisKeys
from services.ai_tools import TOOLS, TOOL_REGISTRY

# We initialize the client dynamically to ensure it picks up changes
def get_ai_client() -> Optional[OpenAI]:
    settings = get_settings()
    # Read from .env manually if settings doesn't pick it up yet without a reload
    api_key = settings.ai_api_key or os.environ.get("AI_API_KEY")
    base_url = settings.ai_base_url or os.environ.get("AI_BASE_URL")
    
    if api_key:
        return OpenAI(
            base_url=base_url if base_url else None,
            api_key=api_key
        )
    return None

async def _get_chat_history(user_id: int) -> List[Dict[str, Any]]:
    key = make_key(RedisKeys.AI_CHAT_HISTORY, user_id=user_id)
    history = await cache_get(key)
    return history if history else []

async def _save_chat_history(user_id: int, history: List[Dict[str, Any]]) -> None:
    key = make_key(RedisKeys.AI_CHAT_HISTORY, user_id=user_id)
    # Keep only last 20 messages to save context limit
    if len(history) > 20:
        # Keep system prompt if present
        if history[0]["role"] == "system":
            history = [history[0]] + history[-19:]
        else:
            history = history[-20:]
    await cache_set(key, history, ttl=86400 * 7) # expire in 7 days

async def chat_with_ai(user_id: int, user_message: str) -> str:
    """
    Main entry point for AI chat.
    Returns the AI's natural language response, OR a JSON string if an action is proposed.
    """
    client = get_ai_client()
    if not client:
        return "❌ AI is not configured. Please add AI_API_KEY to your .env file."
        
    history = await _get_chat_history(user_id)
    
    # System prompt - always ensure the latest agent.md is used
    system_content = "You are the WPAY ADS Personal AI Assistant."
    try:
        with open("agent.md", "r", encoding="utf-8") as f:
            system_content = f.read()
    except Exception:
        pass
        
    if not history:
        history.append({"role": "system", "content": system_content})
    elif history[0]["role"] == "system":
        history[0]["content"] = system_content
    else:
        history.insert(0, {"role": "system", "content": system_content})
        
    history.append({"role": "user", "content": user_message})
    
    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash-free",
            messages=history,
            tools=TOOLS,
            tool_choice="auto"
        )
        
        message = response.choices[0].message
        
        # Create a safe assistant dictionary preserving reasoning_content if present
        assistant_dict = {"role": "assistant", "content": message.content}
        reasoning = getattr(message, "reasoning_content", None)
        if not reasoning and hasattr(message, "model_extra") and message.model_extra:
            reasoning = message.model_extra.get("reasoning_content")
        if reasoning:
            assistant_dict["reasoning_content"] = reasoning

        # Check for tool calls
        if message.tool_calls:
            # Add all tool calls to the assistant message
            assistant_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                } for tc in message.tool_calls
            ]
            history.append(assistant_dict)
            
            # Execute tools
            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except Exception:
                    func_args = {}
                
                if func_name in TOOL_REGISTRY:
                    # Execute tool securely
                    tool_result = await TOOL_REGISTRY[func_name](user_id, func_args)
                    
                    # If action request, return immediately to prompt user
                    if "_action_request" in tool_result:
                        return tool_result
                        
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": str(tool_result)
                    })
                else:
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": "Error: Tool not found."
                    })
                    
            # Call LLM again to get final answer
            final_response = client.chat.completions.create(
                model="deepseek-v4-flash-free",
                messages=history
            )
            final_message = final_response.choices[0].message
            final_dict = {"role": "assistant", "content": final_message.content}
            
            final_reasoning = getattr(final_message, "reasoning_content", None)
            if not final_reasoning and hasattr(final_message, "model_extra") and final_message.model_extra:
                final_reasoning = final_message.model_extra.get("reasoning_content")
            if final_reasoning:
                final_dict["reasoning_content"] = final_reasoning
                
            history.append(final_dict)
            await _save_chat_history(user_id, history)
            return final_message.content or "Done."
                    
        # No tool called
        history.append(assistant_dict)
        await _save_chat_history(user_id, history)
        return message.content or "No response generated."
        
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"
