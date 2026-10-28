# Privacy policy

Workspace MCP is a set of MCP servers that one person runs on their own computer, with an OAuth
client that person creates in their own Google Cloud project.

- **What it reads.** The servers call the Gmail, Calendar, Drive, Docs, Sheets, Slides and People
  APIs only when the person's own MCP client asks a tool to, for the Google accounts the person
  logged in with `google-auth login`.
- **Where data goes.** Data from Google goes to the MCP client on the same computer that called the
  tool, and nowhere else. The project runs no server of its own, collects no analytics, and sends
  nothing to its authors or to any third party.
- **What it stores.** OAuth tokens stay in `~/.google/` on that computer, readable by its owner
  only. The optional audit log of write calls stays on that computer too.
- **Removing access.** Delete the token files in `~/.google/tokens/`, or revoke the app at
  https://myaccount.google.com/permissions.

Questions: agis.pof@gmail.com.
