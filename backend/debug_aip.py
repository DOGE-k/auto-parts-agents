"""调试 AIP 服务端响应"""
import asyncio
import json
import httpx


async def test():
    body = {
        "jsonrpc": "2.0",
        "id": "test-001",
        "method": "task",
        "params": {
            "message": {
                "type": "task-command",
                "id": "cmd-001",
                "sentAt": "2026-09-28T00:00:00Z",
                "senderRole": "leader",
                "senderId": "test-leader",
                "sessionId": "test-session",
                "taskId": "task-debug-001",
                "command": "start",
                "dataItems": [
                    {"type": "text", "text": 'quotation.calculate_cost|{"product_id":"test"}'}
                ]
            }
        }
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://127.0.0.1:8000/aip/quotation/rpc",
            json=body,
        )
        result = resp.json()
        print("=== 响应 ===")
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(test())
