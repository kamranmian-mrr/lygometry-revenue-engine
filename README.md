# Lygometry Revenue Engine v0.1

Goal: discover economically valuable "unknowns", score them, design the cheapest experiment,
and compound only the signals that show evidence of demand.

CORE PRINCIPLE
---------------
Do not choose YouTube, gaming, affiliate, SaaS, newsletter, etc. first.
The engine chooses the opportunity first; the monetization channel follows the evidence.

ZERO-NEW-COST DESIGN
--------------------
- Microsoft Copilot: strategic AI analysis, research, decisioning and content generation.
- Excel/CSV: opportunity memory and experiment ledger.
- GitHub Actions: free automation when used in a public repository.
- Public RSS sources: discovery signals.
- Python standard library: scoring and QA.
- YouTube API is optional later; it has a default daily quota and no separate per-request fee.

IMPORTANT
---------
This package is a tested starter engine. "100% autonomous AI reasoning" requires an AI runtime
that can execute prompts on a schedule. Your paid Microsoft Copilot may provide relevant agent/
workflow features depending on your license/tenant. The core package deliberately does not assume
a separately billed API. Until that AI execution layer is enabled, Copilot is the manager you invoke
for the generated queue.

SETUP
-----
1. Create a PUBLIC GitHub repository.
2. Upload this folder.
3. Enable Actions.
4. Run "Lygometry Discovery" manually once.
5. Open data/opportunities.csv.
6. Paste the highest-potential rows into Copilot using copilot/MASTER_MANAGER_PROMPT.md.
7. Let Copilot produce the experiment brief.
8. Run the cheapest experiment.
9. Record results in data/experiments.csv.
10. Repeat.

The discovery workflow itself requires no paid API keys.
