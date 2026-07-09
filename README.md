# GPT4Free - PA Providers

Welcome to the **PA Providers** repository! This is a collection of custom, drop-in providers for your local `gpt4free` (g4f) installation. 

Share, create, and edit your custom g4f providers easily and fast. Simply drop the providers from this repository into your workspace directory, and call them via your favorite chat interface or directly through the API.

## ✨ Features

- **Drop-in Installation:** Automatically loads any valid provider ending in `*.pa.py`.
- **Sandboxed Execution:** Runs in a secure, sandboxed Python environment with restricted imports and runtime to ensure safety.
- **Live MCP Integration:** Setup a g4f MCP server with built-in tools to create, edit, and manage your custom PA providers. The provider list updates live.
- **Privacy & Security:** Dot-prefixed filenames or directories are never exposed in public-facing methods. Each provider is assigned a stable, opaque ID.
- **API & Chat Support:** Seamlessly use providers via the g4f REST API or connect them to your favorite MCP-compatible chat interface.

---

## 🚀 Quick Start

### 1. Installation
Simply download the `*.pa.py` files from this repository and drop them into your local gpt4free workspace directory. 

The default workspace directory is:
```bash
~/.g4f/workspace
```

### 2. Start the Servers
Enable debug mode and start the API server in your terminal:
```bash
$ g4f api --debug --port 8080
```

*(Optional)* If you want to use the MCP tools to create/edit providers via your chat interface, start the MCP server in a separate terminal:
```bash
$ g4f mcp --http
```

---

## 🛠️ Usage

### Using the REST API

All valid `*.pa.py` files in your workspace are included automatically. 

**List Installed Providers:**
You can get a list of your installed PA providers and their opaque IDs via this URL:
```http
GET http://localhost:8080/pa/providers
```

**Call a Provider:**
Each provider is assigned a **stable opaque ID** derived from the SHA-256 hash of its canonical file path (truncated to 8 hex chars). Use this ID to interact with the provider:
```http
POST http://localhost:8080/api/pa:<stable opaque ID>
```

### Using a Chat Interface (MCP)

If you started the MCP server, you can connect your favorite chat interface (like Cursor, VS Code, or custom clients) to manage providers dynamically.

1. Enter your local MCP address as a custom MCP server in your chat interface settings.
2. The default MCP address is:
   ```text
   http://localhost:8765/mcp
   ```
3. You can now use chat tools to create, edit, and view your providers. The `/providers` list will be live-updated as you make changes.

---

## 📝 Creating a Custom Provider

Providers must inherit from `AsyncGeneratorProvider` and implement the `create_async_generator` method. 

Here is an example of a valid PA provider (`koala.pa.py`):

```python
import json
import aiohttp

from g4f.Provider.base_provider import AsyncGeneratorProvider

class Provider(AsyncGeneratorProvider):
    label = "Koala AI"
    working = True
    url = "https://koala.sh"
    api_url = "https://koala.sh/api/gpt/"
    
    # Define supported models
    models = ["gemini-3-flash", "gpt-5.3-chat-latest", "claude-4.5-haiku"]
    
    async def create_async_generator(self, model, messages, **kwargs):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.api_url,
                json={
                    "messages": messages,
                    "model": model,
                },
                headers={
                    "accept": "text/event-stream",
                    "content-type": "application/json",
                    "flag-real-time-data": "true",
                },
            ) as response:
                async for line in response.content:
                    if line.startswith(b"data: "):
                        yield json.loads(line[6:])
```

### Provider Rules:
1. **File Extension:** Must end with `*.pa.py` to be recognized and auto-included.
2. **Class Name:** The main class must be named `Provider`.
3. **Sandbox:** Remember that the code runs in a sandboxed environment. Standard library and `aiohttp` are generally available, but arbitrary system-level imports are restricted for security.

---

## 🔒 Security & Sandboxing

- **Restricted Runtime:** PA providers run inside a sandboxed Python environment. This restricts dangerous imports and limits runtime capabilities to prevent malicious code execution.
- **Opaque IDs:** To protect your local file structure and provider names, the actual filename is never exposed. Instead, an 8-character hex ID (SHA-256 hash of the canonical path) is used in all API responses and endpoints.

---

## 🤝 Contributing

Feel free to fork this repository, create your own `*.pa.py` providers, and submit Pull Requests to share them with the community! 

*Happy Prompting!*
