import sys
import logging
import json
import os
import aiohttp
from aiohttp import web

logger = logging.getLogger("sitecustomize")

try:
    async def _a2a_health_handler(request):
        return web.json_response({
            "name": "platform-agent",
            "description": "Hermes Platform Agent",
            "version": "1.0.0",
            "status": "ok",
            "url": "http://127.0.0.1:8080",
            "capabilities": {"chat": True, "tools": True, "streaming": True}
        })

    async def _a2a_message_handler(request):
        body = await request.text()
        logger.info("[sitecustomize] A2A POST / message received: headers=%s body=%s", dict(request.headers), body)
        req_id = None
        ctx_id = "default-ctx"
        user_text = ""
        method = ""
        try:
            data = json.loads(body)
            req_id = data.get("id")
            method = data.get("method", "")
            params = data.get("params", {})
            msg = params.get("message", {})
            ctx_id = msg.get("contextId", "default-ctx")
            parts = msg.get("parts", [])
            if parts and isinstance(parts, list):
                user_text = parts[0].get("text", "")
            logger.info("[sitecustomize] Parsed A2A prompt: '%s' (id=%s, method=%s)", user_text, req_id, method)
        except Exception as e:
            logger.error("[sitecustomize] Error parsing A2A body: %s", e)
        
        # 1. Forward prompt asynchronously to Hermes internal /v1/chat/completions using aiohttp.ClientSession!
        ai_text = f"Received: {user_text}"
        try:
            api_key = os.environ.get("API_SERVER_KEY", "")
            req_body = {
                "model": "hermes-agent",
                "messages": [{"role": "user", "content": user_text}]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://127.0.0.1:8080/v1/chat/completions",
                    json=req_body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    },
                    timeout=120
                ) as response:
                    hermes_resp = await response.json()
                    ai_text = hermes_resp["choices"][0]["message"]["content"]
                    logger.info("[sitecustomize] Hermes LLM completion received: '%s'", ai_text)
        except Exception as e:
            logger.exception("[sitecustomize] Failed to query Hermes /v1/chat/completions: %s", e)
            ai_text = f"Hello from Hermes Platform Agent! (LLM execution note: {e})"
        
        resp_msg = {
            "kind": "message",
            "role": "assistant",
            "messageId": "resp-" + str(req_id),
            "contextId": ctx_id,
            "parts": [{"kind": "text", "text": ai_text}]
        }

        # 2. Return A2A v0.3 SSE streaming response where result IS the message object (top-level kind="message")
        if "text/event-stream" in request.headers.get("Accept", "") or method == "message/stream":
            logger.info("[sitecustomize] Streaming SSE response for request %s", req_id)
            resp = web.StreamResponse(status=200, reason="OK", headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "A2a-Version": "0.3"
            })
            await resp.prepare(request)

            # In A2A v0.3 streaming, result MUST have top-level "kind": "message"
            chunk1 = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": resp_msg})
            await resp.write(f"data: {chunk1}\n\n".encode("utf-8"))
            
            chunk2 = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": {"kind": "status", "status": "completed"}})
            await resp.write(f"data: {chunk2}\n\n".encode("utf-8"))
            
            return resp
        else:
            return web.json_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": resp_msg
            }, headers={"A2a-Version": "0.3"})

    old_app_init = web.Application.__init__
    def new_app_init(self, *args, **kwargs):
        old_app_init(self, *args, **kwargs)
        try:
            self.router.add_get("/.well-known/agent-card.json", _a2a_health_handler)
            self.router.add_post("/", _a2a_message_handler)
            self.router.add_post("/a2a", _a2a_message_handler)
            logger.info("[sitecustomize] Successfully added A2A GET and POST routes to web.Application")
        except Exception:
            pass
    web.Application.__init__ = new_app_init
except Exception as e:
    logger.exception("[sitecustomize] Failed to patch web.Application: %s", e)
