#!/usr/bin/env python

import argparse
import contextlib
import functools
import itertools
import re
import string
import sys
import warnings
from collections import namedtuple

import docutils
import docutils.parsers.rst
import docutils.parsers.rst.directives.parts
import sphinx.directives
import sphinx.ext.autodoc.directive
from docutils.parsers.rst import directives, roles

# Handle directives by inserting them into the tree unparsed.


def register_node(cls):
    docutils.nodes._add_node_class_names([cls.__name__])
    return cls


@register_node
class directive(docutils.nodes.Element):
    pass


class generic_directive(docutils.parsers.rst.Directive):
    def run(self):
        return [directive(directive=self)]


# Add support for common directives.


def identity(x):
    return x


def register_directive(name):
    def proc(cls):
        # Make sure all arguments are passed through without change.
        cls.option_spec = {k: identity for k in cls.option_spec}
        directives.register_directive(name, cls)

    return proc


@register_directive("toctree")
class toctree_directive(generic_directive, sphinx.directives.other.TocTree):
    pass


# `list-table` directives are parsed as table nodes and could be formatted as such, but that's
# vulnerable to producing malformed tables when the given column widths are too small. TODO: The
# contents of some directives, including `list-table`, should be parsed and formatted as normal
# reST, but we currently dump all directive bodies unchanged.
@register_directive("list-table")
class listtable_directive(generic_directive, directives.tables.ListTable):
    pass


for d in [
    sphinx.ext.autodoc.ClassDocumenter,
    sphinx.ext.autodoc.ModuleDocumenter,
    sphinx.ext.autodoc.FunctionDocumenter,
    sphinx.ext.autodoc.MethodDocumenter,
]:

    register_directive("auto" + d.objtype)(
        type(
            f"autodoc_{d.objtype}_directive",
            (generic_directive, sphinx.ext.autodoc.directive.AutodocDirective),
            {"option_spec": d.option_spec},
        )
    )


try:
    import sphinxarg.ext

    @register_directive("argparse")
    class argparse_directive(generic_directive, sphinxarg.ext.ArgParseDirective):
        pass


except ImportError:
    pass


@register_directive("contents")
class contents_directive(generic_directive, directives.parts.Contents):
    pass


@register_node
class role(docutils.nodes.Element):
    def __init__(self, rawtext, escaped_text, **options):
        super().__init__(rawtext, escaped_text=escaped_text, options=options)


class DumpVisitor(docutils.nodes.GenericNodeVisitor):
    def __init__(self, document, file=None):
        super().__init__(document)
        self.depth = 0
        self.file = file or sys.stdout

    def default_visit(self, node):
        t = type(node).__name__
        print("    " * self.depth + f"- \x1b[34m{t}\x1b[m", end=" ", file=self.file)
        if isinstance(node, docutils.nodes.Text):
            print(repr(node.astext()[:100]), end="", file=self.file)
        else:
            print(
                {k: v for k, v in node.attributes.items() if v}, end="", file=self.file
            )
        print(file=self.file)

        self.depth += 1

    def default_departure(self, node):
        self.depth -= 1


# Constants.


# The non-overlined characters from https://devguide.python.org/documenting/#sections, plus some.
section_chars = '=-^"~+'

# https://docutils.sourceforge.io/docs/ref/rst/restructuredtext.html#inline-markup-recognition-rules
space_chars = set(string.whitespace)
pre_markup_break_chars = space_chars | set("-:/'\"<([{")
post_markup_break_chars = space_chars | set("-.,:;!?\\/'\")]}>")


# Iterator stuff.


def intersperse(val, it):
    first = True
    for x in it:
        if not first:
            yield val
        first = False
        yield x


chain = itertools.chain.from_iterable


def enum_first(it):
    return zip(itertools.chain([True], itertools.repeat(False)), it)


def prepend_if_any(f, it):
    try:
        x = next(it)
    except StopIteration:
        return
    yield f
    yield x
    yield from it


def chain_intersperse(val, it):
    first = True
    for x in it:
        if not first:
            yield val
        first = False
        yield from x


def pairwise(iterable):
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)


# Helper classes and functions.


class FormatContext(
    namedtuple("FormatContextBase", ["section_depth", "width", "bullet", "colwidths"])
):
    def indent(self, n):
        if self.width is None:
            return self
        return self._replace(width=max(1, self.width - n))

    def in_section(self):
        return self._replace(section_depth=self.section_depth + 1)

    def with_width(self, w):
        return self._replace(width=w)

    def with_bullet(self, bullet):
        return self._replace(bullet=bullet)

    def with_colwidths(self, c):
        return self._replace(colwidths=c)


# Define this here to support Python <3.7.
class nullcontext(contextlib.AbstractContextManager):
    def __init__(self, enter_result=None):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *excinfo):
        pass


class inline_markup:
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "inline_markup({})".format(repr(self.text))


word_info = namedtuple(
    "word_info",
    ["text", "in_markup", "start_space", "end_space", "start_punct", "end_punct"],
)


def split_words(item):
    if isinstance(item, str):
        if not item:
            # An empty string is treated as having trailing punctuation: it only
            # shows up when two inline markup blocks are separated by
            # backslash-space, and this means that after it is merged with its
            # predecessor the resulting word will not cause a second escape to
            # be introduced when merging with the successor.
            new_words = [word_info(item, False, False, False, False, True)]
        else:
            new_words = [
                word_info(s, False, False, False, False, False) for s in item.split()
            ]
            if item:
                if not new_words:
                    new_words = [word_info("", False, True, True, True, True)]
                if item[0] in space_chars:
                    new_words[0] = new_words[0]._replace(start_space=True)
                if item[-1] in space_chars:
                    new_words[-1] = new_words[-1]._replace(end_space=True)
                if item[0] in post_markup_break_chars:
                    new_words[0] = new_words[0]._replace(start_punct=True)
                if item[-1] in pre_markup_break_chars:
                    new_words[-1] = new_words[-1]._replace(end_punct=True)
    elif isinstance(item, inline_markup):
        new_words = [
            word_info(s, True, False, False, False, False) for s in item.text.split()
        ]
    return new_words


def wrap_text(width, items):
    items = list(items)
    raw_words = list(chain(map(split_words, items)))

    words = [word_info("", False, True, True, True, True)]
    for word in raw_words:
        last = words[-1]
        if not last.in_markup and word.in_markup and not last.end_space:
            join = "" if last.end_punct else r"\ "
            words[-1] = word_info(
                last.text + join + word.text, True, False, False, False, False
            )
        elif last.in_markup and not word.in_markup and not word.start_space:
            join = "" if word.start_punct else r"\ "
            words[-1] = word_info(
                last.text + join + word.text,
                False,
                False,
                word.end_space,
                word.start_punct,
                word.end_punct,
            )
        else:
            words.append(word)

    words = (word.text for word in words if word.text)

    if width is None:
        yield " ".join(words)
        return

    buf = []
    n = 0
    for w in words:
        n2 = n + bool(buf) + len(w)
        if buf and n2 > width:
            yield " ".join(buf)
            buf = []
            n2 = len(w)
        buf.append(w)
        n = n2
    if buf:
        yield " ".join(buf)


def fmt_children(node, ctx):
    return (fmt(c, ctx) for c in node.children)


def with_spaces(n, lines):
    s = " " * n
    for l in lines:
        yield s + l if l else l


def preproc(node):
    """
    Do some node preprocessing that is generic across node types and is therefore most convenient to
    do as a simple recursive function rather than as part of the big dispatcher class.
    """
    node.children = [
        c for c in node.children if not isinstance(c, docutils.nodes.system_message)
    ]
    for c in node.children:
        preproc(c)

    for a, b in pairwise(node.children):
        if isinstance(a, docutils.nodes.reference) and isinstance(
            b, docutils.nodes.target
        ):
            a.attributes["target"] = b


# Main stuff.


class Formatters:
    # Basic formatting.
    @staticmethod
    def substitution_reference(node, ctx: FormatContext):
        yield inline_markup("|" + "".join(chain(fmt_children(node, ctx))) + "|")

    @staticmethod
    def emphasis(node, ctx: FormatContext):
        yield inline_markup("*" + "".join(chain(fmt_children(node, ctx))) + "*")

    @staticmethod
    def strong(node, ctx: FormatContext):
        yield inline_markup("**" + "".join(chain(fmt_children(node, ctx))) + "**")

    @staticmethod
    def literal(node, ctx: FormatContext):
        yield inline_markup("``" + "".join(chain(fmt_children(node, ctx))) + "``")

    @staticmethod
    def title_reference(node, ctx: FormatContext):
        yield inline_markup("`" + "".join(chain(fmt_children(node, ctx))) + "`")

    # Lists.
    @staticmethod
    def bullet_list(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx.with_bullet("- ")))

    @staticmethod
    def enumerated_list(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx.with_bullet("#.")))

    @staticmethod
    def list_item(node, ctx: FormatContext):
        w = len(ctx.bullet) + 1
        b = ctx.bullet + " "
        s = " " * w
        ctx = ctx.indent(w)
        for first, c in enum_first(chain_intersperse("", fmt_children(node, ctx))):
            yield ((b if first else s) if c else "") + c

    @staticmethod
    def term(node, ctx: FormatContext):
        yield " ".join(wrap_text(0, chain(fmt_children(node, ctx))))

    @staticmethod
    def definition(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx))

    @staticmethod
    def definition_list_item(node, ctx: FormatContext):
        for c in node.children:
            if isinstance(c, docutils.nodes.term):
                yield from fmt(c, ctx)
            elif isinstance(c, docutils.nodes.definition):
                yield from with_spaces(3, fmt(c, ctx.indent(3)))

    @staticmethod
    def definition_list(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx))

    # Structure.
    @staticmethod
    def paragraph(node, ctx: FormatContext):
        yield from wrap_text(ctx.width, chain(fmt_children(node, ctx)))

    @staticmethod
    def title(node, ctx: FormatContext):
        text = " ".join(wrap_text(0, chain(fmt_children(node, ctx))))
        yield text
        yield section_chars[ctx.section_depth - 1] * len(text)

    @staticmethod
    def block_quote(node, ctx: FormatContext):
        yield from with_spaces(
            3, chain_intersperse("", fmt_children(node, ctx.indent(3))),
        )

    @staticmethod
    def directive(node, ctx: FormatContext):
        d = node.attributes["directive"]

        yield " ".join([f".. {d.name}::", *d.arguments])
        # Just rely on the order being stable, hopefully.
        for k, v in d.options.items():
            yield f"   :{k}:" if v is None else f"   :{k}: {v}"
        yield from prepend_if_any("", with_spaces(3, d.content))

    @staticmethod
    def section(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx.in_section()))

    @staticmethod
    def document(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx))

    # Tables.
    @staticmethod
    def row(node, ctx: FormatContext):
        all_lines = [
            chain_intersperse("", fmt_children(entry, ctx.with_width(w - 2)))
            for entry, w in zip(node.children, ctx.colwidths)
        ]
        for line_group in itertools.zip_longest(*all_lines):
            yield "|" + "|".join(
                " " + (line or "").ljust(w - 2) + " "
                for line, w in zip(line_group, ctx.colwidths)
            ) + "|"

    @staticmethod
    def tbody(node, ctx: FormatContext):
        sep = "+" + "+".join("-" * w for w in ctx.colwidths) + "+"
        yield from chain_intersperse(sep, fmt_children(node, ctx))

    thead = tbody

    @staticmethod
    def tgroup(node, ctx: FormatContext):
        ctx = ctx.with_colwidths(
            [
                c.attributes["colwidth"]
                for c in node.children
                if isinstance(c, docutils.nodes.colspec)
            ]
        )
        sep = "+" + "+".join("-" * w for w in ctx.colwidths) + "+"

        yield sep
        for c in node.children:
            if isinstance(c, docutils.nodes.colspec):
                continue
            if isinstance(c, docutils.nodes.thead):
                yield from fmt(c, ctx)
                yield "+" + "+".join("=" * w for w in ctx.colwidths) + "+"
            if isinstance(c, docutils.nodes.tbody):
                yield from fmt(c, ctx)
                yield sep

    @staticmethod
    def table(node, ctx: FormatContext):
        yield from chain_intersperse("", fmt_children(node, ctx))

    # Misc.
    @staticmethod
    def Text(node, _: FormatContext):
        yield node.astext()

    @staticmethod
    def reference(node, ctx: FormatContext):
        title = " ".join(wrap_text(0, chain(fmt_children(node, ctx))))
        anonymous = (
            ("target" not in node.attributes)
            if "refuri" in node.attributes
            else (node.attributes.get("anonymous"))
        )
        suffix = "__" if anonymous else "_"

        if "refuri" in node.attributes:
            uri = node.attributes["refuri"]
            # Do a basic check for standalone hyperlinks.
            if uri == title or uri == "mailto:" + title:
                yield inline_markup(title)
            else:
                yield inline_markup(f"`{title} <{uri}>`{suffix}")
        else:
            # Reference names can consist of "alphanumerics plus isolated (no two adjacent) internal
            # hyphens, underscores, periods, colons and plus signs", according to
            # https://docutils.sourceforge.io/docs/ref/rst/restructuredtext.html#reference-names.
            is_single_word = (
                re.match("^[-_.:+a-zA-Z]+$", title) and not re.search("[-_.:+][-_.:+]", title)
            ) or (
                len(node.children) == 1
                and isinstance(node.children[0], docutils.nodes.substitution_reference)
            )
            if not is_single_word:
                title = "`" + title + "`"
            yield inline_markup(title + suffix)

    @staticmethod
    def role(node, ctx: FormatContext):
        yield inline_markup(node.rawsource)

    @staticmethod
    def inline(node, ctx: FormatContext):
        yield from chain(fmt_children(node, ctx))

    @staticmethod
    def target(node, ctx: FormatContext):
        try:
            body = " " + node.attributes["refuri"]
        except KeyError:
            body = ""
        if isinstance(node.parent, (docutils.nodes.document, docutils.nodes.section)):
            yield f".. _{node.attributes['names'][0]}:" + body

    @staticmethod
    def comment(node, ctx: FormatContext):
        yield ".."
        text = "\n".join(chain(fmt_children(node, ctx)))
        yield from with_spaces(3, text.split("\n"))

    @staticmethod
    def note(node, ctx: FormatContext):
        yield ".. note::"
        yield ""
        yield from with_spaces(
            3, chain_intersperse("", fmt_children(node, ctx.indent(3)))
        )

    @staticmethod
    def warning(node, ctx: FormatContext):
        yield ".. warning::"
        yield ""
        yield from with_spaces(
            3, chain_intersperse("", fmt_children(node, ctx.indent(3)))
        )

    @staticmethod
    def hint(node, ctx: FormatContext):
        yield ".. hint::"
        yield ""
        yield from with_spaces(
            3, chain_intersperse("", fmt_children(node, ctx.indent(3)))
        )

    @staticmethod
    def image(node, ctx: FormatContext):
        yield f".. image:: {node.attributes['uri']}"

    @staticmethod
    def literal_block(node, ctx: FormatContext):
        lang = [c for c in node.attributes["classes"] if c != "code"]
        yield ".. code::" + (" " + lang[0] if lang else "")
        yield ""
        text = "".join(chain(fmt_children(node, ctx)))
        yield from with_spaces(3, text.split("\n"))


def fmt(node, ctx: FormatContext):
    func = getattr(
        Formatters,
        type(node).__name__,
        lambda _, __: ["\x1b[35m{}\x1b[m".format(type(node).__name__.upper())],
    )
    return func(node, ctx)


def format_node(width, node):
    return "\n".join(fmt(node, FormatContext(0, width, None, None)))


def parse_string(s):
    parser = docutils.parsers.rst.Parser()
    doc = docutils.utils.new_document(
        "",
        settings=docutils.frontend.OptionParser(
            components=(docutils.parsers.rst.Parser,)
        ).get_default_values(),
    )
    parser.parse(s, doc)
    preproc(doc)

    return doc


def dump_node(node, file):
    node.walkabout(DumpVisitor(node, file))


def node_eq(d1, d2):
    if type(d1) is not type(d2):
        print("different type")
        return False
    if len(d1.children) != len(d2.children):
        print("different num children")
        for i, c in enumerate(d1.children):
            print(1, i, c)
        for i, c in enumerate(d2.children):
            print(2, i, c)
        return False
    if not all(node_eq(c1, c2) for c1, c2 in zip(d1.children, d2.children)):
        return False
    return True


def run_test(doc):
    if isinstance(doc, str):
        doc = parse_string(doc)

    for width in [1, 2, 3, 5, 8, 13, 34, 55, 89, 144, 72, None]:
        output = format_node(width, doc)
        doc2 = parse_string(output)
        output2 = format_node(width, doc2)

        try:
            assert node_eq(doc, doc2)
            assert output == output2
        except AssertionError:
            with open("/tmp/dump1.txt", "w") as f:
                dump_node(doc, f)
            with open("/tmp/dump2.txt", "w") as f:
                dump_node(doc2, f)

            with open("/tmp/out1.txt", "w") as f:
                print(output, file=f)
            with open("/tmp/out2.txt", "w") as f:
                print(output2, file=f)

            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--in-place", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-w", "--width", type=int, default=72)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()

    if args.width <= 0:
        args.width = None

    for r in ["class", "download", "func", "ref", "superscript"]:
        roles.register_canonical_role(r, roles.GenericRole(r, role))

    STDIN = "-"

    for fn in args.files or [STDIN]:
        cm = nullcontext(sys.stdin) if fn == STDIN else open(fn)
        with cm as f:
            doc = parse_string(f.read())

        if args.verbose:
            print("=" * 60, fn, file=sys.stderr)
            dump_node(doc, sys.stderr)

        if args.test:
            try:
                run_test(doc)
            except AssertionError as e:
                raise AssertionError(f"Failed consistency test on {fn}!") from e

        output = format_node(args.width, doc)

        if fn != STDIN and args.in_place:
            cm = open(fn, "w")
        else:
            cm = nullcontext(sys.stdout)
            if fn == STDIN and args.in_place:
                warnings.warn("Cannot edit stdin in place; writing to stdout!")

        with cm as f:
            print(output, file=f)