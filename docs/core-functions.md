# NIST AI RMF — The Four Core Functions

## GOVERN

Cultivates and implements a culture of risk management across the
organization. Unlike MAP, MEASURE, and MANAGE, GOVERN's outcomes apply
throughout the AI lifecycle and are foundational to the other three —
policies, accountability structures, and risk tolerance should be in place
*before* an organization maps risks for a specific system.

Example outcomes: legal and regulatory requirements involving AI are
understood and managed; roles and responsibilities for AI risk management
are clear; processes exist for third-party AI systems and components.

## MAP

Establishes the context to frame risks related to a specific AI system:
intended purpose, deployment context, requirements, and the range of
stakeholders affected — including those who may be impacted but do not
directly interact with the system.

Example outcomes: the AI system's business value and intended use are
understood; capabilities, benefits, and costs are documented relative to
existing methods; risks and benefits are mapped for all identified
stakeholders.

## MEASURE

Employs quantitative, qualitative, or mixed-method tools to analyze,
assess, benchmark, and monitor AI risk and related impacts. This includes
tracking metrics for trustworthy AI characteristics (e.g. measuring bias,
robustness to adversarial input, or explainability) and revisiting them as
the system or its context changes.

Example outcomes: appropriate methods and metrics are identified and
applied; risks from third-party software or data are measured; feedback
from affected communities is gathered and assessed.

## MANAGE

Allocates risk-management resources to mapped and measured risks on a
regular basis, and prioritizes responding to the risks with the greatest
potential for harm. Includes deciding whether to proceed with a system, and
planning for incident response, recovery, and communication.

Example outcomes: risks are prioritized and responded to based on impact;
resources are allocated to manage risks according to established plans;
mechanisms exist to sustain the value of deployed AI systems (including
decommissioning when risk outweighs benefit).

## Why This Matters for Distributed Tracing (meta note)

This repository is not really about the AI RMF itself — it's a vehicle for
exploring distributed tracing with LangSmith. There's a real parallel,
though: the AI RMF's MEASURE function is about instrumenting a system well
enough to know whether it's actually trustworthy in practice, not just in
design — which is exactly what tracing is for on the engineering side. A
request that starts in the TypeScript frontend and continues in a Python
agent needs a shared trace ID to prove the two events are the same causal
chain, the same way an AI system needs concrete, measured evidence (not just
a policy document) to back up a claim of trustworthiness.
