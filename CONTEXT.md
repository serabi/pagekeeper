# PageKeeper Context

## Book Intake Module

The Book Intake Module is the domain Module that turns a user's matching or import intent into a PageKeeper `Book`.

Its Interface is organized around user intent: map audiobook plus ebook, import audio-only, import ebook-only, attach an ebook, and attach an audiobook. The Module owns the implementation details behind that seam: Grimmory lookup and shelf updates, KoSync hash computation and preservation, duplicate merge migration, Storyteller reservation and async submission, Hardcover automatch, ABS collection updates, suggestion resolution, and initial book status.

This Depth gives route callers leverage: blueprints parse requests and choose redirects or errors, while intake side effects stay local. The intended locality is that future changes to intake ordering or cross-service side effects happen in `src/services/book_intake_service.py`, not in matching routes.
