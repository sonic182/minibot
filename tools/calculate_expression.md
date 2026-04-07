# calculate_expression

## Purpose

Safely evaluates arithmetic expressions with Decimal precision.

## Availability

Enabled by default through `[tools.calculator].enabled = true`.

## Configuration

Relevant config: `[tools.calculator]`.

Important fields include `default_scale`, `max_expression_length`, and `max_exponent_abs`.

## Interface

Inputs:

- `expression`: arithmetic expression to evaluate.
- `scale`: optional Decimal precision.

Supported operators are `+`, `-`, `*`, `/`, `%`, `**`, parentheses, and unary plus/minus.

## Safety Notes

The tool parses and validates arithmetic syntax instead of evaluating arbitrary Python. The hidden legacy alias `calculator` is normalized to this tool at execution time.
