---
site: patch
www: patch
admin: patch
---

- When the server can't be reached, signing in and the account pages (continue, onboarding, invitations, confirming a browser) say "Syruss can't be reached right now" with a way to try again, instead of showing the web framework's error. Forms on those pages show the same sentence.
- When the sign-in service is down, signing in comes back to the front page with that message, signing out still ends the session, and a short outage no longer signs anyone out of the apps.
- Every app has an error page of its own: a page that fails to load says so plainly and offers Try again.
- When the browser gets no answer at all (offline), the apps say "Can't reach Syruss. Check your connection and try again."
