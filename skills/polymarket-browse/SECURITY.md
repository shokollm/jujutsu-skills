# Security Policy

## Security Audit (2026-03-25)

This document tracks security issues found during the 2026-03-25 audit.

## Fixed Issues

| Issue | Severity | Fixed Date | Fix |
|-------|----------|------------|-----|
| Telegram bot token in process command line | CRITICAL | 2026-03-25 | Switched to Python urlopen from curl subprocess |
| HTML injection in Telegram messages | HIGH | 2026-03-25 | Added escape_html() function |
| Insufficient --search URL encoding | MEDIUM | 2026-03-26 | Use urllib.parse.quote() |
| --detail bounds not validated | MEDIUM | 2026-03-26 | Error on out of range |
| No response size limits | MEDIUM | 2026-03-26 | MAX_RESPONSE_SIZE check |
| Bare except: clauses | LOW | 2026-03-26 | Catch specific exceptions |
| No API rate limiting | LOW | 2026-03-26 | TokenBucket rate limiter |

## Open Issues

All security issues from this audit have been addressed in subsequent releases.

## Reporting Security Issues

If you find a security vulnerability, please report it by opening an issue.
