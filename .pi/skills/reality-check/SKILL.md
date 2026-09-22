---
name: reality-check
description: "Provide balanced perspective on any proposal or request by stating at least one realistic pro and one realistic con. Use this skill when the user mentions 'reality check' or explicitly requests a balanced view of tradeoffs."
---

# Reality Check Skill

When you invoke this skill—by mentioning "reality check" or "tradeoff" or asking for balanced perspective, —I will always provide at least one genuine advantage and one genuine drawback for whatever you're asking me to help with.

## Purpose

This skill ensures you get honest tradeoff analysis instead of one-sided enthusiasm or dismissal. It's your built-in sanity check: before you commit to a direction, you'll see both the upside that makes it worth considering and the downside that makes it worth watching.

## When to Use

Invoke this skill when:
- You explicitly ask for a "reality check" on an idea, plan, or proposal
- You want tradeoff analysis before making a decision
- You're about to implement something and want to hear what could go wrong and what could go right

## What to do

When invoked, I will:

1. **State at least one pro**: A genuine, realistic advantage or benefit of what you've asked about. Not hype—something that actually works in your favor or makes the approach sensible.

2. **State at least one con**: A genuine, realistic drawback, risk, or limitation. Not a deal-breaker necessarily—just what you're trading away or accepting.

3. **Keep both grounded**: Both the pro and con connect to your actual situation and context, not abstract possibilities.

## Format

The output is plain prose:
- A brief opening line naming what I'm reality-checking
- **Pro:** one or more genuine advantages
- **Con:** one or more genuine drawbacks
- Optional closing: practical next steps or questions to ask yourself

Example:
> **Reality check: Using matplotlib instead of building a custom visualization**
>
> **Pro:** Matplotlib plots render quickly, the library is stable and well-documented, and you can iterate on the plot style without touching your analysis code.
>
> **Con:** Matplotlib plots often need tweaking to look publication-ready, and the learning curve for advanced customization is steep. You'll spend time on styling that a custom solution might get right once.

## Verification

- Both the pro and con are real—not invented to sound balanced
- The analysis is specific to your context, not generic
- I don't soften the con to make it sound minor, and I don't oversell the pro to make it sound risk-free
- If one factor genuinely outweighs the other in your case, the analysis reflects that
