# tahidromos-mcp

An MCP server that gives an AI agent a **local, disposable email inbox**.

Agents that handle email need somewhere to practise. The existing options are
production inbox services — real addresses, real delivery, a bill, and an
internet connection. This points an agent at a mail server on your laptop,
where nothing can escape and a counterparty that always answers is one call
away.

```sh
pip install tahidromos-mcp
```

You also need the mail server itself:

```sh
docker run -d -p 1025:25 -p 1587:587 -p 1143:143 -p 8080:8080 \
  ghcr.io/pamfilico/tahidromos
```

## Configure

Claude Desktop, or anything else that speaks MCP over stdio:

```json
{
  "mcpServers": {
    "tahidromos": {
      "command": "tahidromos-mcp",
      "env": { "TAHIDROMOS_URL": "http://localhost:8080" }
    }
  }
}
```

Claude Code:

```sh
claude mcp add tahidromos -- tahidromos-mcp
```

## Tools

| Tool | |
| --- | --- |
| `create_inbox` | A disposable address for one task |
| `list_inboxes` · `delete_inbox` · `cleanup` | Manage them |
| `wait_for_email` | Block until a matching message arrives |
| `list_emails` · `read_email` · `list_threads` | Read |
| `send_email` · `reply_to_email` · `forward_email` | Write |
| `echo_bot_address` | An address that always replies |
| `send_test_scenario` · `list_test_scenarios` | Deliver awkward mail on purpose |
| `check_spam_score` | Score a message, with reasons |
| `server_status` | Is it up, and how is it configured |

`wait_for_email` blocks on the server, so an agent calls it once and waits
rather than polling — which is both faster and cheaper in tokens.

Replies and forwards build their own `In-Reply-To`, `References` and `Re:`/
`Fwd:` prefixes, so an agent never has to construct mail headers to hold a
correctly threaded conversation.

## A session looks like this

```
create_inbox(purpose="support")        → support-a1b2c3@tahidromos.test
echo_bot_address()                     → echo@tahidromos.test
send_email(from, to=echo, "Ticket #42", "Is this fixed?")
wait_for_email(address, in_reply_to=<the id>)   → the reply, threaded
reply_to_email(address, uid, "Thanks, closing it.")
cleanup()
```

Nothing in that reached the internet, cost anything, or needed a second person.

## Environment

| Variable | Default |
| --- | --- |
| `TAHIDROMOS_URL` | `http://localhost:8080` |
| `TAHIDROMOS_RUN_ID` | `mcp` — groups the inboxes `cleanup` removes |

## License

MIT
