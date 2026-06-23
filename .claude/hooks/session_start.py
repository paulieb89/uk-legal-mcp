#!/usr/bin/env python3
"""
SessionStart hook: prints orientation context at the start of every Claude Code session.
Reminds the agent to run /audit before starting work.
"""
import sys

print("""
╔══════════════════════════════════════════════════════╗
║          uk-legal-mcp - Claude Code Session          ║
╠══════════════════════════════════════════════════════╣
║  BEFORE starting work: run /audit                    ║
║                                                      ║
║  Commands:  /audit  /probe  /new-tool  /bug          ║
║  Rules:     .claude/rules/  (loaded per file path)   ║
║                                                      ║
║  Hooks active: syntax check + tests on every edit    ║
╚══════════════════════════════════════════════════════╝
""", file=sys.stderr)

sys.exit(0)
