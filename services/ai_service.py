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
    
    # System prompt
    if not history:
        history.append({
            "role": "system", 
            "content": (
                "You are the WPAY ADS Personal AI Assistant. You help the user manage their Telegram bulk marketing accounts. "
                "You have access to tools to fetch stats and propose actions. "
                "Keep responses concise, highly analytical, friendly, and use formatting/emojis where appropriate."
            )
        })
        
    history.append({"role": "user", "content": user_message})
    
    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash-free",
            messages=history,
            tools=TOOLS,
            tool_choice="auto"
        )
        
        message = response.choices[0].message
        
        # Check for tool calls
        if message.tool_calls:
            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                
                if func_name in TOOL_REGISTRY:
                    # Execute tool securely (user_id is injected, NEVER provided by AI)
                    tool_result = await TOOL_REGISTRY[func_name](user_id, func_args)
                    
                    # If it's an action request, intercept and return to caller to prompt user with Action Queue
                    if "_action_request" in tool_result:
                        return tool_result
                        
                    # Otherwise, it's a READ tool, feed it back to the LLM
                    # The OpenAI API requires passing back the tool call format
                    history.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_call.function.arguments
                                }
                            }
                        ]
                    })
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": tool_result
                    })
                    
                    # Call LLM again to get final answer
                    final_response = client.chat.completions.create(
                        model="deepseek-v4-flash-free",
                        messages=history
                    )
                    final_message = final_response.choices[0].message
                    history.append({"role": "assistant", "content": final_message.content})
                    await _save_chat_history(user_id, history)
                    return final_message.content or "Done."
                    
        # No tool called
        history.append({"role": "assistant", "content": message.content})
        await _save_chat_history(user_id, history)
        return message.content or "No response generated."
        
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"
