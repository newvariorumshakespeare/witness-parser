# Witness Parser

This repository contains functions for parsing NVS witness lists and textual notes to build a complete list of chronologically-ordered witnesses and a complete list of chronologically-ordered witnesses for each variant reading.

Its primary purpose is as an editorial aid.

The `witness_parser` module can be imported in any Python script. Its two primary functions are:

- `get_witness_list()`: Reads the front matter file and returns a list of all witnesss **not chronologically ordered**.
- `parse_notes()`: Reads the front matter and textual notes files and returns a dictionary with entries for each reading in each note. For each reading there is a chronologically-ordered list of witnesses containing that reading.

For convenience, the `workflow.ipynb` notebook allows easy configuration and execution of the functions, as well as a cell for saving the output of `parse_notes()` to a JSON file.
