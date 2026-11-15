---
admin: patch
www: patch
---

- **Live chats no longer crash when a chat opens.** Scrolling to the newest message ran in an effect that returned the browser's answer to React ("destroy is not a function").
- **Changing your password from Settings asks for your current one plainly.** Keycloak first confirms it's you, but its page looked like a normal sign-in ("Welcome back", "Password", "Sign in"). People typed their new password there and got "Invalid username or password". It now says "Confirm it's you", asks for your **Current password**, says you'll choose the new one on the next page, and its button reads Continue. The same page shows when setting up or removing an authenticator.
