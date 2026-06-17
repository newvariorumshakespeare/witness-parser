"""witness.py.

Utilities for parsing TEI critical-apparatus witness formulas.

A `<wit>` element in a TEI apparatus entry contains a *witness formula*
that describes which witnesses share a particular reading.  The formula
uses three named character entities:

`&sigrange;`
    Inclusive range between two sigla as ordered in the edition witness list:
    `<siglum>F1</siglum>&sigrange;<siglum>F4</siglum>` → F1, F2, F3, F4.

`&plus;`
    The preceding siglum and every witness that follows it in the list:
    `<siglum>F3</siglum>&plus;` → F3 onwards.

`&minus;`
    Exclusion marker placed inside a parenthesised group; the whole group
    is subtracted from the set built up so far:
    `(&minus;<siglum>hal</siglum>, <siglum>alex</siglum>)`

Parentheses may enclose any comma-separated combination of individual sigla,
ranges (`&sigrange;`), or "all-from" expressions (`&plus;`), optionally
preceded by `&minus;` or `&plus;` to mark the group as an exclusion or
explicit addition.

Last Update: June 17, 2026
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

# Mapping of custom entities to their respective Unicode characters (additional entities can be added here as needed)
# Note that &sigrange;, &plus;, and &minus; are handled separately in the tokenization step, so they are not included here.
XML_ENTITY_MAP = {
    "&hellip;": " .\u00a0.\u00a0. ",
    "&inked;": "\u2759",
    "&caret;": "\u2038",
    "&shy;": "\u00ad",
    "&swdash;": "\u2002~\u2002",
    "&cmacr;": "c\u0304",
    "&ptilde;": "p\u0303",
    "&mtilde;": "m\u0303",
    "&mmacron;": "m\u0304",
    "&emacrondot;": "\u0113\u0323",
    "&asteriskmacron;": "*\u0304",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_witness_list(
    xml_source: str | Path, show_dates: bool = False
) -> list[str] | list[tuple[str, str]]:
    """Read `<listWit>` witness lists from an XML file and return sigla.

    Args:
        xml_source (str | Path): Path to a TEI XML file containing one or more
            `<listWit>` elements.
        show_dates (bool): If True, return a list of tuples mapping sigla to dates
            instead of a sorted list of sigla.

    Returns:
        list[str] | list[tuple[str, str]]: List of all witness sigla found in `<witness>`
        elements, sorted by date, or a list of tuples mapping sigla to dates if `show_dates` is True.
    """
    # Open and parse the XML source using BeautifulSoup with the lxml-xml parser
    with open(xml_source, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml-xml")

    sigla: dict[str, str] = {}

    # Find all <listWit> elements anywhere in the tree
    for list_wit in soup.find_all("listWit"):
        # Find all direct or nested <witness> tags inside this <listWit>
        for witness_el in list_wit.find_all("witness"):
            # Check for <siglum> first, fallback to witness text
            siglum_el = witness_el.find("siglum")
            if siglum_el is not None:
                # Assuming get_text gets all nested text or element content
                value = siglum_el.get_text(strip=True)
            else:
                value = witness_el.get_text(strip=True)

            if not value:
                continue

            # Look for a <date> child element
            date_el = witness_el.find("date")
            if date_el is None:
                continue

            date_value = date_el.get_text(strip=True)
            if not date_value:
                continue

            if value not in sigla:
                sigla[value] = date_value

    sigla_sorted = smart_date_sorted(list(sigla.items()))

    if show_dates:
        return sigla_sorted
    return [sig for sig, _ in sigla_sorted]


def parse_notes(notes_xml_path: str | Path, front_xml_path: str | Path) -> list[dict]:
    """Parse TEI textual notes from an XML file into structured dictionaries.

    Args:
        notes_xml_path (str | Path): Path to the TEI XML textual notes file. The file may contain an internal DTD subset with entity declarations — these are resolved automatically by the parser.
        front_xml_path (str | Path): Path to the TEI XML file containing the `<listWit>` elements that define the witness sigla and their dates.

    Returns:
        list[dict]: One dictionary per `<note>` element.
    """
    witness_list_tuples = get_witness_list(front_xml_path, show_dates=True)
    witness_dict = dict(witness_list_tuples)
    witness_list = [sig for sig, _ in witness_list_tuples]

    # Pre-process raw text to resolve custom entities before parsing
    with open(notes_xml_path, "r", encoding="utf-8") as f:
        notes_raw_text = f.read()
        for entity_str, unicode_char in XML_ENTITY_MAP.items():
            notes_raw_text = notes_raw_text.replace(entity_str, unicode_char)

    notes_soup = BeautifulSoup(notes_raw_text, "lxml-xml")

    with open(front_xml_path, "r", encoding="utf-8") as f:
        front_soup = BeautifulSoup(f, "lxml-xml")

    # Find only top-level <note> elements, ignoring nested inline <note> elements
    top_level_notes = [
        n
        for n in notes_soup.find_all("note")
        if not (n.has_attr("place") and n["place"] == "inline")
    ]

    return [
        _parse_single_note(note_el, front_soup, witness_list, witness_dict)
        for note_el in top_level_notes
    ]


def parse_notes_to_xml(
    notes_xml_path: str | Path,
    front_xml_path: str | Path,
    replace_wit: bool = False,
    remove_wit: bool = False,
) -> str:
    """Parse textual notes and return a modified XML string with witness id references.

    The returned XML preserves the original note/appPart/rdg/wit structure as far as
    possible, while adding a `witnesses` attribute to each `<appPart>` element.
    If `replace_wit` is True, the original `<wit>` content is replaced with a new
    `<wit>` containing one `<siglum>` child per witness that carries the reading.
    If `remove_wit` is True, the `<wit>` element is removed entirely after the
    `witnesses` attribute is added.
    The attribute value is a space-separated list of `#xml:id` references for the
    witnesses that carry the reading.

    Args:
        notes_xml_path (str | Path): Path to the TEI XML textual notes file.
        front_xml_path (str | Path): Path to the TEI XML file containing the
            `<listWit>` legend with witness definitions.
        replace_wit (bool): If True, replace each original `<wit>` element with a
            synthesized `<wit>` element containing explicit `<siglum>` entries.
        remove_wit (bool): If True, remove the `<wit>` element entirely from each
            `<appPart>`.

    Returns:
        str: XML string containing the modified notes document.
    """
    witness_list_tuples = get_witness_list(front_xml_path, show_dates=True)
    witness_dict = dict(witness_list_tuples)
    witness_list = [sig for sig, _ in witness_list_tuples]

    # Parse the textual notes and front matter.
    with open(notes_xml_path, "r", encoding="utf-8") as f:
        notes_raw_text = f.read()
        for entity_str, unicode_char in XML_ENTITY_MAP.items():
            notes_raw_text = notes_raw_text.replace(entity_str, unicode_char)
        notes_soup = BeautifulSoup(notes_raw_text, "lxml-xml")

    with open(front_xml_path, "r", encoding="utf-8") as f:
        front_soup = BeautifulSoup(f, "lxml-xml")

    witness_id_map = _build_witness_id_map(front_soup)

    for note_el in notes_soup.find_all("note"):
        for app_part in note_el.find_all("appPart"):
            wit_el = app_part.find("wit")
            if wit_el is None:
                continue

            witness_sigla = _resolve_witness_sigla_for_ids(
                wit_el, front_soup, witness_list, witness_dict
            )
            if not witness_sigla:
                continue

            witness_refs = []
            for sig in witness_sigla:
                xml_id = witness_id_map.get(sig)
                witness_refs.append(f"#{xml_id}" if xml_id else sig)

            app_part["witnesses"] = " ".join(witness_refs)

            if not remove_wit and replace_wit:
                new_wit = notes_soup.new_tag("wit", **wit_el.attrs)
                for index, sig in enumerate(witness_sigla):
                    siglum_el = notes_soup.new_tag("siglum")
                    siglum_el.string = sig
                    new_wit.append(siglum_el)
                    if index < len(witness_sigla) - 1:
                        new_wit.append(", ")
                wit_el.replace_with(new_wit)
            elif remove_wit:
                wit_el.decompose()

    # Preserve the full modified document if possible, otherwise emit the note elements.
    return str(notes_soup)


def _build_witness_id_map(root: BeautifulSoup) -> dict[str, str]:
    """Build a siglum-to-xml:id mapping from `<listWit>` witness definitions."""
    witness_id_map: dict[str, str] = {}
    for list_wit in root.find_all("listWit"):
        for witness_el in list_wit.find_all("witness"):
            siglum_el = witness_el.find("siglum")
            if siglum_el is not None:
                siglum = siglum_el.get_text(strip=True)
            else:
                siglum = witness_el.get_text(strip=True)

            if not siglum:
                continue

            xml_id = witness_el.get("xml:id") or witness_el.get("id")
            if xml_id:
                witness_id_map[siglum] = xml_id

    return witness_id_map


def _resolve_witness_sigla_for_ids(
    wit_el: Tag,
    front_soup: BeautifulSoup,
    witness_list: list[str],
    witness_dict: dict[str, str],
) -> list[str]:
    """Resolve the list of witness sigla for an `<appPart>` without inline-note annotations."""
    witnesses = parse_witness_formula(wit_el, witness_list)
    witnesses = _expand_collective_sigla(witnesses, front_soup, witness_list)
    witnesses_tuples = smart_date_sorted(
        [(sig, witness_dict.get(sig, "0")) for sig in witnesses]
    )
    return [sig for sig, _ in witnesses_tuples]


def parse_witness_formula(
    wit_xml: str | Tag,
    witness_list: list[str],
) -> list[str]:
    """Parse a TEI `<wit>` witness formula and return all sigla with the reading.

    Args:
        wit_xml (str | Tag): The `<wit>` element as a raw XML string *or* a
            pre-parsed `bs4.Tag`. When a string is supplied the named entities
            `&sigrange;`, `&plus;`, and `&minus;` are substituted with inline
            placeholder elements before parsing.
        witness_list (list[str]): Ordered list of **all** witness sigla in the edition.
            The order governs range expansion (`&sigrange;`) and "all from here onwards"
            expansion (`&plus;`).

    Returns:
        list[str]: Sigla that carry the reading, returned in witness-list order.
    """
    if isinstance(wit_xml, str):
        # Replace named entities with placeholder void elements
        xml_str = wit_xml.replace("&sigrange;", "<_sigrange/>")
        xml_str = xml_str.replace("&plus;", "<_plus/>")
        xml_str = xml_str.replace("&minus;", "<_minus/>")

        # Parse the string with BeautifulSoup using the lxml-xml parser
        soup = BeautifulSoup(xml_str, "lxml-xml")

        # Select the topmost tag generated by the fragment string
        root = soup.find()
    else:
        root = wit_xml

    # If root is None (empty string input), return an empty list safely
    if root is None:
        return []

    # Process using the tokenization and evaluation helpers updated for Tag objects
    tokens = _tokenize_wit(root)
    included = _eval_tokens(tokens, witness_list)

    order = {w: i for i, w in enumerate(witness_list)}
    return sorted(included, key=lambda w: order.get(w, len(witness_list)))


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _annotate_witnesses(root: BeautifulSoup, witnesses: list[str]) -> list[str]:
    """Append an asterisk to witnesses in a `listWit` source with xml:id="listwit_other"."""
    other_sigla: set[str] = set()

    # Direct target selector for <listWit> containing xml:id="listwit_other"
    # Matches both standard id and escaped namespaced xml:id attributes
    other_listwit = root.select_one(
        "listWit[id='listwit_other'], listWit[xml\\:id='listwit_other']"
    )

    if other_listwit is not None:
        # Find all <witness> tags nested inside this specific listWit container
        for witness_el in other_listwit.find_all("witness"):
            # Extract siglum or fallback to witness text node content
            siglum_el = witness_el.find("siglum")
            if siglum_el is not None:
                value = siglum_el.get_text(strip=True)
            else:
                value = witness_el.get_text(strip=True)

            if value:
                other_sigla.add(value)

    # Append an asterisk to the witness if it matches any siglum found in the "other" list
    return [f"{sig}*" if sig in other_sigla else sig for sig in witnesses]


def _expand_collective_sigla(
    witnesses: list[str], listwit_root: BeautifulSoup, witness_list: list[str]
) -> list[str]:
    """Expand collective sigla based on the `corresp` attribute in the `listWit` elements.

    Args:
        witnesses (list[str]): The list of witness sigla to expand.
        listwit_root (BeautifulSoup): The parsed XML document or element
            containing the `listWit` elements.
        witness_list (list[str]): The ordered list of all witness sigla in the edition.

    Returns:
        list[str]: The expanded list of witness sigla.
    """
    expanded = set()

    for sig in witnesses:
        if sig in witness_list:
            expanded.add(sig)
            continue

        sig_id = f"s_{sig.lower()}"

        # Use CSS selectors to target the specific witness ID directly.
        # BeautifulSoup matches namespaced 'xml:id' attributes via standard id selectors.
        witness_el = listwit_root.select_one(
            f"witness[id='{sig_id}'], witness[xml\\:id='{sig_id}']"
        )

        if witness_el is None:
            continue

        corresp = witness_el.get("corresp")
        if not corresp:
            continue

        for item in corresp.split():
            item = item.lstrip("#")
            if not item:
                continue

            # Direct O(1) or fast-native tree lookups for the target corresponding element
            corresp_el = listwit_root.select_one(
                f"witness[id='{item}'], witness[xml\\:id='{item}']"
            )

            if corresp_el is None:
                continue

            # Look for a direct or nested <siglum> element
            siglum_el = corresp_el.find("siglum")
            if siglum_el is not None:
                value = siglum_el.get_text(strip=True)
            else:
                value = corresp_el.get_text(strip=True)

            if value:
                expanded.add(value)

    # Return as a list to match the original type signature constraints
    return list(expanded)


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_OPERATOR_TAGS = {"sigrange", "plus", "minus"}


def _tokenize_wit(element: Tag) -> list[tuple[str, str | None]]:
    """Flatten the element tree into an ordered token stream.

    Args:
        element (Tag): The root of the witness formula element tree.

    Returns:
        list[tuple[str, str | None]]: A list of tokens, where each token is a tuple
            containing the token type and an optional value.
    """
    tokens: list[tuple[str, str | None]] = []
    _collect(element, tokens)
    return tokens


def _collect(node: Tag, tokens: list) -> None:
    """Recursively walk *node*, appending tokens to *tokens*.

    Args:
        node (Tag): The current element node being processed.
        tokens (list): The list of tokens to append to.
    """
    # Iterate through all direct child nodes (both text nodes and element tags)
    # in the exact order they appear in the source XML file.
    for child in node.contents:
        # Case 1: The child is a text node (NavigableString)
        if isinstance(child, NavigableString):
            # Pass the raw text block directly into your token converter
            _chars_to_tokens(str(child), tokens)

        # Case 2: The child is an XML tag element (Tag)
        elif isinstance(child, Tag):
            # BeautifulSoup splits namespaces naturally, keeping tag names clean.
            # Strip the leading underscore used by our placeholder entities.
            tag = child.name.lstrip("_")

            if tag == "siglum":
                sigil = child.get_text(strip=True)
                if sigil:
                    tokens.append(("SIGLUM", sigil))

            elif tag in _OPERATOR_TAGS:
                tokens.append((tag.upper(), None))  # SIGRANGE | PLUS | MINUS

            else:
                # Recurse into any other container wrappers (e.g., nested tags)
                _collect(child, tokens)


def _chars_to_tokens(text: str, tokens: list) -> None:
    """Emit structural tokens from raw character data.

    Handles both the string-substitution path (where operators are already
    emitted as elements) and the pre-parsed-element path (where the XML
    parser has resolved the DTD entities to their Unicode code points):

    * `-` (U+002D) — `&sigrange;` resolves to HYPHEN-MINUS
    * `+` (U+002B) — `&plus;` resolves to PLUS SIGN
    * `\u2212` (U+2212) — `&minus;` resolves to MINUS SIGN

    Args:
        text (str): The raw character data to process.
        tokens (list): The list of tokens to append to.
    """
    for ch in text:
        if ch == "(":
            tokens.append(("LPAREN", None))
        elif ch == ")":
            tokens.append(("RPAREN", None))
        elif ch == ",":
            tokens.append(("COMMA", None))
        elif ch == "-":  # &sigrange; → U+002D
            tokens.append(("SIGRANGE", None))
        elif ch == "+":  # &plus;     → U+002B
            tokens.append(("PLUS", None))
        elif ch == "\u2212":  # &minus;    → U+2212 MINUS SIGN
            tokens.append(("MINUS", None))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _eval_tokens(
    tokens: list[tuple[str, str | None]],
    witness_list: list[str],
) -> set[str]:
    """Reduce a token stream to the set of included witnesses.

    Args:
        tokens (list[tuple[str, str | None]]): The list of tokens to evaluate.
        witness_list (list[str]): The ordered list of all witness sigla in the edition.

    Returns:
        set[str]: The set of included witnesses.
    """
    result: set[str] = set()
    pos = 0
    n = len(tokens)

    while pos < n:
        tok, val = tokens[pos]

        # ------------------------------------------------------------------ #
        # Siglum — may be followed by &sigrange; … or &plus;                  #
        # ------------------------------------------------------------------ #
        if tok == "SIGLUM":
            if (
                pos + 2 < n
                and tokens[pos + 1][0] == "SIGRANGE"
                and tokens[pos + 2][0] == "SIGLUM"
            ):
                result.update(_expand_range(val, tokens[pos + 2][1], witness_list))
                pos += 3
            elif pos + 1 < n and tokens[pos + 1][0] == "PLUS":
                result.update(_expand_from(val, witness_list))
                pos += 2
            else:
                result.add(val)
                pos += 1

        # ------------------------------------------------------------------ #
        # Parenthesised group: (&minus; …) or (&plus; …) or (…)              #
        # ------------------------------------------------------------------ #
        elif tok == "LPAREN":
            pos += 1

            # Optional leading group modifier
            modifier = None
            if pos < n and tokens[pos][0] in ("MINUS", "PLUS"):
                modifier = tokens[pos][0]
                pos += 1

            group: set[str] = set()
            while pos < n and tokens[pos][0] != "RPAREN":
                t, v = tokens[pos]
                if t == "SIGLUM":
                    if (
                        pos + 2 < n
                        and tokens[pos + 1][0] == "SIGRANGE"
                        and tokens[pos + 2][0] == "SIGLUM"
                    ):
                        group.update(_expand_range(v, tokens[pos + 2][1], witness_list))
                        pos += 3
                    elif pos + 1 < n and tokens[pos + 1][0] == "PLUS":
                        group.update(_expand_from(v, witness_list))
                        pos += 2
                    else:
                        group.add(v)
                        pos += 1
                else:
                    pos += 1  # skip COMMA / stray MINUS / PLUS inside group

            if pos < n:
                pos += 1  # consume RPAREN

            if modifier == "MINUS":
                result -= group
            else:
                result |= group  # PLUS or no modifier → additive

        else:
            pos += 1  # skip top-level COMMA / stray operators

    return result


# ---------------------------------------------------------------------------
# Note parsing helpers
# ---------------------------------------------------------------------------


def _parse_single_note(
    note_el: Tag, front_soup: BeautifulSoup, witness_list: list, witness_dict: dict
) -> dict:
    """Extracts attributes and child elements for a single top-level <note> element.

    Args:
        note_el (Tag): The <note> element to parse.
        front_soup (BeautifulSoup): The parsed XML document containing the <listWit> elements for witness expansion and annotation.
        witness_list (list): The ordered list of all witness sigla in the edition.
        witness_dict (dict): A mapping of sigla to their corresponding dates for sorting purposes.

    Returns:
        dict: A dictionary containing the attributes of the <note> element, its label, lemma, and a list of readings with their associated witnesses.
    """
    note_data = {k: v for k, v in note_el.attrs.items()}

    label_el = note_el.find("label")
    if label_el is not None:
        note_data["label"] = label_el.get_text(" ", strip=True)

    app_el = note_el.find("app")
    if app_el is not None:
        lem_el = app_el.find("lem")
        if lem_el is not None:
            note_data["lem"] = lem_el.get_text(" ", strip=True)

        note_data["readings"] = _parse_app_readings(
            app_el, front_soup, witness_list, witness_dict
        )

    return note_data


def _parse_app_readings(
    app_el: Tag, front_soup: BeautifulSoup, witness_list: list, witness_dict: dict
) -> list[dict]:
    """Extracts all <appPart> readings inside a specific <app> block.

    Args:
        app_el (Tag): The <app> element containing the readings to parse.
        front_soup (BeautifulSoup): The parsed XML document containing the <listWit> elements for witness expansion and annotation.
        witness_list (list): The ordered list of all witness sigla in the edition.
        witness_dict (dict): A mapping of sigla to their corresponding dates
            for sorting purposes.

    Returns:
        list[dict]: A list of dictionaries, one per <appPart>, each containing the reading text, optional reading type, and the list of witnesses associated with that reading.
    """
    readings = []
    for part in app_el.find_all("appPart"):
        reading = {}

        rdg_el = part.find("rdg")
        if rdg_el is not None:
            reading["rdg"] = re.sub("\\s+", " ", rdg_el.get_text(" ", strip=True))
            if rdg_el.has_attr("type"):
                reading["rdg_type"] = rdg_el["type"]
        else:
            rdg_desc_el = part.find("rdgDesc")
            if rdg_desc_el is not None:
                reading["rdg"] = re.sub(
                    "\\s+", " ", rdg_desc_el.get_text(" ", strip=True)
                )

        wit_el = part.find("wit")
        if wit_el is not None:
            reading["wit"] = _process_witnesses_with_inline_notes(
                wit_el, front_soup, witness_list, witness_dict
            )

        if reading:
            readings.append(reading)

    return readings


def _process_witnesses_with_inline_notes(
    wit_el: Tag, front_soup: BeautifulSoup, witness_list: list, witness_dict: dict
) -> list[str]:
    """Handles parsing and sorting, while rewriting entries when inline notes are found.

    Args:
        wit_el (Tag): The <wit> element to process, which may contain an inline note.
        front_soup (BeautifulSoup): The parsed XML document containing the <listWit> elements for witness expansion and annotation.
        witness_list (list): The ordered list of all witness sigla in the edition.
        witness_dict (dict): A mapping of sigla to their corresponding dates for sorting purposes.

    Returns:
        list[str]: The final list of witness sigla, sorted and annotated, with inline notes appended to the appropriate sigla when present.
    """
    inline_note_el = wit_el.find("note", attrs={"place": "inline"})

    if inline_note_el is None:
        return _process_witnesses(wit_el, front_soup, witness_list, witness_dict)

    inline_text = inline_note_el.get_text(" ", strip=True)
    prev_siglum_el = inline_note_el.find_previous("siglum")
    target_siglum = prev_siglum_el.get_text(" ", strip=True) if prev_siglum_el else None

    witnesses = parse_witness_formula(wit_el, witness_list)
    witnesses = _expand_collective_sigla(witnesses, front_soup, witness_list)

    witnesses_tuples = smart_date_sorted(
        [(sig, witness_dict.get(sig, "0")) for sig in witnesses]
    )
    sorted_witnesses = [sig for sig, _ in witnesses_tuples]
    annotated_witnesses = _annotate_witnesses(front_soup, sorted_witnesses)

    final_witnesses = []
    for sig in annotated_witnesses:
        clean_sig = re.sub(r"[^a-zA-Z0-9]", "", sig).lower()
        clean_target = (
            re.sub(r"[^a-zA-Z0-9]", "", target_siglum).lower() if target_siglum else ""
        )

        if target_siglum and clean_sig == clean_target:
            final_witnesses.append(f"{sig} {inline_text}")
        else:
            final_witnesses.append(sig)

    return final_witnesses


def _process_witnesses(
    wit_el: Tag, front_soup: BeautifulSoup, witness_list: list, witness_dict: dict
) -> list[str]:
    """Resolves, sorts, and annotates a standard witness list (no inline notes).

    Args:
        wit_el (Tag): The <wit> element to process.
        front_soup (BeautifulSoup): The parsed XML document containing the <listWit> elements for witness expansion and annotation.
        witness_list (list): The ordered list of all witness sigla in the edition.
        witness_dict (dict): A mapping of sigla to their corresponding dates for sorting purposes.

    Returns:
        list[str]: The final list of witness sigla, sorted and annotated.
    """
    witnesses = parse_witness_formula(wit_el, witness_list)
    witnesses = _expand_collective_sigla(witnesses, front_soup, witness_list)

    witnesses_tuples = smart_date_sorted(
        [(sig, witness_dict.get(sig, "0")) for sig in witnesses]
    )
    sorted_witnesses = [sig for sig, _ in witnesses_tuples]

    return _annotate_witnesses(front_soup, sorted_witnesses)


# ---------------------------------------------------------------------------
# Range helpers
# ---------------------------------------------------------------------------


def _expand_range(start: str, end: str, witness_list: list[str]) -> list[str]:
    """Return all witnesses between *start* and *end* inclusive.

    If either siglum is absent from the list it is returned as a bare
    singleton rather than raising an error.  Reversed ranges (end before
    start) are handled gracefully.

    Args:
        start (str): The siglum at the start of the range.
        end (str): The siglum at the end of the range.
        witness_list (list[str]): The ordered list of all witness sigla in the edition.

    Returns:
        list[str]: The list of witnesses between *start* and *end* inclusive.
    """
    try:
        i = witness_list.index(start)
    except ValueError:
        return [start]
    try:
        j = witness_list.index(end)
    except ValueError:
        return [end]
    return witness_list[i : j + 1] if i <= j else witness_list[j : i + 1]


def _expand_from(start: str, witness_list: list[str]) -> list[str]:
    """Return *start* and every witness that follows it in *witness_list*.

    Args:
        start (str): The siglum at the start of the expansion.
        witness_list (list[str]): The ordered list of all witness sigla in the edition.

    Returns:
        list[str]: The list of witnesses from *start* to the end of the list.
    """
    try:
        return witness_list[witness_list.index(start) :]
    except ValueError:
        return [start]


# ---------------------------------------------------------------------------
# Date sorting helpers
# ---------------------------------------------------------------------------


def smart_date_sorted(data_list: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Sort a list of (siglum, date) tuples by date, handling various date formats.

    Args:
        data_list (list[tuple[str, str]]): The list of (siglum, date) tuples to sort.

    Returns:
        list[tuple[str, str]]: The sorted list of (siglum, date) tuples.
    """

    def get_sort_key(item: tuple[str, str]) -> tuple[int, int, int, str]:
        """Generate a sort key for a (siglum, date) tuple based on the date string.

        Args:
            item (tuple[str, str]): A tuple containing the siglum and date string.

        Returns:
            tuple: A composite key for sorting.
        """
        _, orig_date = item

        # Standardize en-dashes to hyphens
        norm_date = orig_date.replace("–", "-").strip()

        # 1. Feature Flag Detection
        has_question_mark = "?" in norm_date
        starts_with_dash = norm_date.startswith("-")
        is_pure_digit = norm_date.isdigit()

        # Detect if it's exactly a single year wrapped in brackets (e.g., "[1619]")
        is_bracketed_single = (
            norm_date.startswith("[")
            and norm_date.endswith("]")
            and norm_date[1:-1].isdigit()
        )

        # 2. Extract Base Primary Year
        clean_date = re.sub(r"[\[\]\?c\.\s]", "", norm_date)
        year_match = re.search(r"\d+", clean_date)
        base_year = int(year_match.group()) if year_match else 0

        # 3. Enhanced Tier System for Base Year Ordering
        # Tier 0: Starts with "-" (BCE / Precedes all)
        # Tier 1: Single date in brackets (e.g., "[1619]")
        # Tier 2: Pure digits (e.g., "1619")
        # Tier 3: Anything else (Ranges, approximations, e.g., "1619-", "c. 1619")
        if starts_with_dash:
            tier = 0
            # For BCE dates, a larger base_year means further in the past.
            # Inverting the base_year keeps Tier 0 sorted chronologically.
            base_year = -base_year
        elif is_bracketed_single:
            tier = 1
        elif is_pure_digit:
            tier = 2
        else:
            tier = 3

        # 4. Tie-breaking flag for question marks
        q_flag = 1 if has_question_mark else 0

        # FIXED: Sort by tier FIRST so chronological eras do not bleed into each other,
        # followed by the base year, question mark flag, and string fallback.
        return (tier, base_year, q_flag, norm_date)

    return sorted(data_list, key=get_sort_key)
