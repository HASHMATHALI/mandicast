"""Serving: train the frozen forecasting method for the current quarter, register it, and publish weekly forecasts.

Kept outside `mandicast.forecast` so the SHA-256 of the evaluated forecasting code (recorded at freezing and checked
at the hold-out evaluation) stays unchanged; this package only calls that code.
"""
