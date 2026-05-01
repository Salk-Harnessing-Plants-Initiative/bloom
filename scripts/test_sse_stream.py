"""
Test SSE streaming from the Bloom langchain agent.
Shows every event as it arrives — status, tool calls, tokens, completion.

Usage:
    python scripts/test_sse_stream.py "Say hello"
    python scripts/test_sse_stream.py "List available experiments"
    python scripts/test_sse_stream.py "Run a QC report on experiment foo" --provider local
    python scripts/test_sse_stream.py "What's the mean primary root length?" --tool-set generic

Prints time-to-first-token and total wall-clock so you can compare against
the synchronous /langchain/chat endpoint.
"""

import argparse
import json
import time
import jwt
import httpx


def make_token(secret: str) -> str:
    return jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000001",
            "role": "authenticated",
            "aud": "authenticated",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )


def main():
    parser = argparse.ArgumentParser(description="Test SSE streaming against /langchain/chat/stream")
    parser.add_argument("prompt", help="The question to ask")
    parser.add_argument("--url", default="http://localhost:5002")
    parser.add_argument("--provider", default="openai", choices=["openai", "local"])
    parser.add_argument("--model", default=None, help="Model name (server picks default if omitted)")
    parser.add_argument("--tool-set", default="all")
    parser.add_argument("--jwt-secret", default="super-secret-jwt-token-with-at-least-32-characters-long")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    token = make_token(args.jwt_secret)
    url = f"{args.url}/langchain/chat/stream"

    print(f"URL: {url}")
    print(f"Provider: {args.provider}")
    print(f"Model: {args.model or '(server default)'}")
    print(f"Tool set: {args.tool_set}")
    print(f"Prompt: {args.prompt}")
    print(f"Timeout: {args.timeout}s")
    print("=" * 60)

    start = time.time()
    first_token_time = None
    token_count = 0
    tools = []

    payload = {
        "prompt": args.prompt,
        "provider": args.provider,
        "tool_set": args.tool_set,
        "mcp_tool_names": [],
        "thread_id": f"test-sse-{int(time.time())}",
    }
    if args.model:
        payload["model"] = args.model

    with httpx.Client(timeout=args.timeout) as client:
        with client.stream(
            "POST",
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json=payload,
        ) as resp:
            print(f"Status: {resp.status_code}")
            print(f"Headers: content-type={resp.headers.get('content-type')}")
            print("-" * 60)

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue

                elapsed = time.time() - start
                try:
                    event = json.loads(line[6:])
                    etype = event.get("type", "?")

                    if etype == "status":
                        print(f"[{elapsed:6.1f}s] STATUS: {event['content']}")
                    elif etype == "tool":
                        tools.append(event["content"])
                        print(f"[{elapsed:6.1f}s] TOOL START: {event['content']}")
                    elif etype == "tool_done":
                        print(f"[{elapsed:6.1f}s] TOOL DONE: {event['content']}")
                    elif etype == "token":
                        if first_token_time is None:
                            first_token_time = elapsed
                            print(f"[{elapsed:6.1f}s] FIRST TOKEN (time to first token: {elapsed:.1f}s)")
                        token_count += 1
                        print(event["content"], end="", flush=True)
                    elif etype == "done":
                        print(f"\n[{elapsed:6.1f}s] DONE. Tools: {event.get('tools_used', [])}")
                    elif etype == "error":
                        print(f"\n[{elapsed:6.1f}s] ERROR: {event['content']}")
                    else:
                        print(f"[{elapsed:6.1f}s] UNKNOWN: {event}")

                except json.JSONDecodeError:
                    print(f"[{elapsed:6.1f}s] RAW: {line}")

    total = time.time() - start
    print("\n" + "=" * 60)
    print(f"Total time:  {total:.1f}s")
    print(f"First token: {first_token_time:.1f}s" if first_token_time else "No tokens received")
    print(f"Tokens:      {token_count}")
    print(f"Tools used:  {tools}")


if __name__ == "__main__":
    main()
