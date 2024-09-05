# DharmaNexus Pali segments Repository

This repository contains the Pali segments input files for DharmaNexus. 

## File Naming Convention

The filenames in this repository follow a specific format to ensure consistency and ease of parsing across all languages. Each filename is structured as follows:

Where:
- `LL` is a two-letter language token
- `category` is the category name
- `filename` is the specific filename

For example: `PA_dn_4.tsv` or `SA_T06_sthmavt.tsv`

## Purpose of This Format

This naming convention is designed to:

1. Maintain consistency across all languages in the DharmaNexus project.
2. Allow for easy parsing of language, category, and filename information without resorting to regular expressions.
3. Minimize the risk of exceptions that could break parsing logic.

## Parsing File Information

With this format, you can reliably extract language, category, and filename information by simply splitting the filename on underscores.

Example (in Python):
```python
filename = "pn_dn_4"
lang, category, text = filename.split('_')
