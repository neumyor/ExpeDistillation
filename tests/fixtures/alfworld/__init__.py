"""Recorded ALFWorld adapter protocol fixtures.

Each .txt file is a verbatim child stdout transcript (one protocol message
per line, exactly as ADAPTER_SOURCE writes them: tag + space + JSON payload).
They record the protocol shape -- handshake, observations, end markers, fatal
error -- so the parsing layer is tested without the eval venv.
"""
