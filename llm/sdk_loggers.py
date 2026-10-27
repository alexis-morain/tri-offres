"""Loggers of the Anthropic SDK and its HTTP client. At DEBUG they log whole requests, CV content included.

Read by settings.LOGGING before the apps load and by the clamp in llm/api.py: imports nothing.
"""
SDK_LOGGERS = ("anthropic", "httpx2", "httpcore2")
