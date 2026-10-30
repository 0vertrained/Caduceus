# 3. MCP is the integration boundary, and access is enforced on the server side

Status: Accepted, built (`src/caduceus/tools.py`, `src/caduceus/mcp_servers.py`)

## Context

The agent needs to read the EHR, the referral packet and coverage policies, change ticket state and talk to the
payer. In a real deployment each of those is a different customer system with its own owner. The agent must not
be able to do anything the human using it could not, and some actions (submitting to a payer) must not be
available at all until a person has approved.

## Decision

* Each customer system is exposed as a Model Context Protocol server (`ehr`, `documents`, `policies`, `tickets`,
  `payer`), built on the MCP Python SDK's `MCPServer`. The servers accept bearer tokens (`JwtTokenVerifier`) and
  derive the caller's role from the token, not from anything the model says.
* Every tool call passes through one gateway that checks, in order: the caller's role may use the tool, and the
  current workflow step allows the tool (enrichment, drafting, review, submission, follow-up). Each call, refusal
  and not-allowed attempt is written to the audit log.
* `payer.submit` additionally requires an audit event recording a PA specialist's approval of this case.

## Consequences

* A prompt-injected instruction ("submit this now") fails at the gateway even if the model follows it. The
  red-team suite tries it three ways: an instruction planted in a visit note, `payer.submit` called from the
  drafting step, and `payer.submit` with no recorded approval. None succeeds.
* The same tools serve the in-process workflow and external MCP clients, so the boundary is tested once.
* MCP adds a hop and a protocol to learn. For three service lines, direct function calls would be simpler; the
  boundary is chosen because a real deployment's systems belong to other teams.

## Alternatives considered

* **Framework-specific tool plugins.** Couples integrations to one agent framework. Not chosen.
* **Role checks in the prompt.** Not a control. Not chosen.
