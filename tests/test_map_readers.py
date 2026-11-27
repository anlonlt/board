"""M4: readers beyond code for `kit.py map` (scripts/kit_map.py): Power BI, documents, any folder.

Each fixture is a small project with faults planted in it; the map must flag each fault (the bar: flags found out of
faults planted) and nothing on the parts left clean. Power BI comes as a PBIP project (TMDL model, PBIR report), as
a legacy one (model.bim, report.json), and as a project that lives only in the service (a .pbix, a link): there the map
says what it needs instead of mapping nothing.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import kit_map  # noqa: E402


def rd(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def visual(name, vtype, fields):
    """A PBIR visual.json using [(kind, table, property)]."""
    proj = [{"field": {k: {"Expression": {"SourceRef": {"Entity": t}}, "Property": p}}} for k, t, p in fields]
    return json.dumps({"name": name, "visual": {"visualType": vtype,
                                                "query": {"queryState": {"Values": {"projections": proj}}}}})


PBI = {
    "Sales.pbip": '{"artifacts": [{"report": {"path": "Sales.Report"}}]}',
    "Sales.SemanticModel/definition/model.tmdl": "model Model\n\tculture: en-US\n",
    "Sales.SemanticModel/definition/tables/Sales.tmdl": (
        "table Sales\n"
        "\tlineageTag: 1\n\n"
        "\tmeasure 'Total Sales' = SUM(Sales[Amount])\n\t\tformatString: 0\n\n"
        "\tmeasure Margin =\n\t\t\t[Total Sales] - SUM(Sales[Cost])\n\n"
        "\tmeasure 'Old KPI' = COUNTROWS(Sales)\n\n"            # FAULT 2: used by nothing
        "\tcolumn Amount\n\t\tdataType: decimal\n\n"
        "\tcolumn Cost\n\t\tdataType: decimal\n\n"
        "\tcolumn CustomerId\n\t\tdataType: int64\n\n"
        "\tcolumn DateKey\n\t\tdataType: int64\n\n"
        "\tcolumn ShipDateKey\n\t\tdataType: int64\n\n"
        "\tpartition Sales = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
        "\t\t\t\t    Source = Sql.Database(\"sql.corp.example\", \"dw\")\n\t\t\t\tin\n\t\t\t\t    Source\n"),
    "Sales.SemanticModel/definition/tables/Customer.tmdl": (
        "table Customer\n\n\tcolumn Id\n\t\tdataType: int64\n\n\tcolumn Name\n\t\tdataType: string\n\n"
        "\tpartition Customer = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
        "\t\t\t\t    Source = Csv.Document(File.Contents(\"C:\\Users\\marie\\Desktop\\customers.csv\"))\n"   # FAULT 5
        "\t\t\t\tin\n\t\t\t\t    Source\n"),
    "Sales.SemanticModel/definition/tables/Date.tmdl": (
        "table Date\n\n\tcolumn DateKey\n\t\tdataType: int64\n\n\tcolumn Year\n\t\tdataType: int64\n\n"
        "\tpartition Date = m\n\t\tsource =\n\t\t\t\tlet\n"
        "\t\t\t\t    Source = SharePoint.Files(\"https://corp.sharepoint.com/sites/bi\")\n\t\t\t\tin\n\t\t\t\t    Source\n"),
    "Sales.SemanticModel/definition/tables/Budget.tmdl": (
        "table Budget\n\n\tmeasure 'Budget Total' = SUM(Budget[Value])\n\n\tcolumn Value\n\t\tdataType: decimal\n\n"
        "\tcolumn Region\n\t\tdataType: string\n"),
    "Sales.SemanticModel/definition/relationships.tmdl": (
        "relationship r1\n\tfromColumn: Sales.CustomerId\n\ttoColumn: Customer.Id\n\n"
        "relationship r2\n\tfromColumn: Sales.DateKey\n\ttoColumn: Date.DateKey\n\n"
        "relationship r3\n\tfromColumn: Sales.ShipDateKey\n\ttoColumn: Date.DateKey\n\n"   # FAULT 3: second active one
        "relationship r4\n\tisActive: false\n\tfromColumn: Sales.ShipDateKey\n\ttoColumn: Customer.Id\n"),
    "Sales.Report/definition.pbir": '{"datasetReference": {"byPath": {"path": "../Sales.SemanticModel"}}}',
    "Sales.Report/definition/pages/p1/page.json": '{"name": "p1", "displayName": "Overview"}',
    "Sales.Report/definition/pages/p1/visuals/v1/visual.json": visual("v1", "card", [("Measure", "Sales", "Total Sales")]),
    "Sales.Report/definition/pages/p1/visuals/v2/visual.json": visual(
        "v2", "barChart", [("Column", "Customer", "Name"), ("Measure", "Sales", "Margin")]),
    "Sales.Report/definition/pages/p1/visuals/v3/visual.json": visual(
        "v3", "card", [("Measure", "Sales", "Profit YoY")]),                                # FAULT 1: no such measure
    "Sales.Report/definition/pages/p2/page.json": '{"name": "p2", "displayName": "Budget"}',
    "Sales.Report/definition/pages/p2/visuals/v4/visual.json": visual(
        "v4", "table", [("Column", "Customer", "Name"), ("Measure", "Budget", "Budget Total")]),  # FAULT 4: no path
    "service.apps.json": '{"value": [{"name": "Sales app", "reports": [{"name": "Sales"}]}]}',
}
PBI_FAULTS = [("pbi-missing-measure", "Profit YoY"), ("pbi-unused-measure", "Old KPI"),
              ("pbi-ambiguous-relationship", "Date"), ("pbi-missing-relationship", "Budget"),
              ("pbi-personal-source", "marie")]

LEGACY = {
    "Ops.SemanticModel/model.bim": json.dumps({"model": {
        "tables": [{"name": "Tickets", "columns": [{"name": "Id"}, {"name": "Hours"}],
                    "measures": [{"name": "Hours Total", "expression": "SUM(Tickets[Hours])"},
                                 {"name": "Unused", "expression": "1"}],
                    "partitions": [{"source": {"expression": ["let", "S = Excel.Workbook(File.Contents(\"/Users/jo/Downloads/t.xlsx\"))", "in S"]}}]}],
        "relationships": []}}),
    "Ops.Report/report.json": json.dumps({"sections": [{"displayName": "Main", "visualContainers": [
        {"config": json.dumps({"name": "c1", "singleVisual": {"visualType": "card", "projections": {"Values": [
            {"queryRef": "Tickets.Hours Total"}]}, "prototypeQuery": {"Select": [
            {"Measure": {"Expression": {"SourceRef": {"Entity": "Tickets"}}, "Property": "Hours Total"}}]}}})}]}]}),
}
LEGACY_FAULTS = [("pbi-unused-measure", "Unused"), ("pbi-personal-source", "/Users/jo")]

SERVICE_ONLY = {"README.md": "# Finance dashboard\n\nThe report: https://app.powerbi.com/groups/abc/reports/def\n",
                "Finance.pbix": "PK\x03\x04 binary"}

BIB = "@article{smith2020, title={A}}\n@book{doe2019, title={B}}\n"
THESIS = {
    "refs.bib": BIB,
    "thesis.tex": r"""\documentclass{report}
\begin{document}
\chapter{Introduction}
Prior work shows the effect is large \cite{smith2020}. Others disagree \citep{jones2021}.
\begin{itemize}
\item \textbf{RQ1}: does the effect hold across schools?
\item \textbf{RQ2}: does it last after a year?
\end{itemize}
\input{chapters/results}
\chapter{Method}
We follow \cite{doe2019}, see Figures~\ref{fig:design} and~\ref{fig:old}.
\begin{figure}
% script: scripts/design.py
\includegraphics{figures/design.png}
\caption{Design.}\label{fig:design}
\end{figure}
\begin{figure}
% script: scripts/old_plot.py
\includegraphics{figures/old.png}
\caption{Old.}\label{fig:old}
\end{figure}
\chapter{Discussion}
For RQ1 the effect holds.
\bibliography{refs}
\end{document}
""",
    "chapters/results.tex": r"""\section{Results}
\begin{figure}
\includegraphics{figures/scores.png}
\caption{Scores.}\label{fig:scores}
\end{figure}
""",
    "scripts/design.py": 'import pandas as pd\nd = pd.read_csv("data/schools.csv")\nplt.savefig("figures/design.png")\n',
    "scripts/scores.py": 'd = pd.read_csv("data/scores.csv")\nplt.savefig("figures/scores.png")\n',
    "data/schools.csv": "id,n\n1,2\n",
    "data/scores.csv": "id,s\n1,2\n",
    "notes.md": "# Notes\n\nA claim from the literature [@smith2020; @lee2018].\n\n![Map](figures/map.png){#fig:map}\n\nSee @fig:map.\n",
}
# planted: jones2021 and lee2018 not in refs.bib; fig:scores never referenced; scripts/old_plot.py gone; RQ2 unanswered
THESIS_FAULTS = [("doc-missing-citation", "jones2021"), ("doc-missing-citation", "lee2018"),
                 ("doc-figure-unreferenced", "fig:scores"), ("doc-figure-script-gone", "old_plot.py"),
                 ("doc-question-unanswered", "RQ2")]


def make_docx(path, paras):
    """A minimal Word file: [(style or "", text)]."""
    body = "".join('<w:p>%s<w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (
        '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % st if st else "", t) for st, t in paras)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % body)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)


WORD_PARAS = [("Heading1", "Introduction"), ("", "Reading improves with practice [@smith2020]."),
              ("", "RQ1: which practice helps most?"), ("", "Figure 1 shows the plan."),
              ("", "Figure 1: The plan."), ("", "Figure 2: An extra chart."),
              ("Heading1", "Findings"), ("", "On RQ1, spaced practice helps [@ghost2022].")]
WORD_FAULTS = [("doc-missing-citation", "ghost2022"), ("doc-figure-unreferenced", "Figure 2")]

FOLDER = {
    "README.md": "# Team folder\n\nSee [the plan](plan.md) and [budget](money/budget.csv) and [old](archive/gone.md).\n",
    "plan.md": "# Plan\n\n## Q1\n\nShip it. Back to [readme](README.md).\n",
    "money/budget.csv": "item,cost\na,1\n",
    "logs/huge.txt": "line\n" * (kit_map.BIG_FILE + 10),
}
FOLDER_FAULTS = [("big-file", "huge.txt"), ("broken-link", "archive/gone.md")]


class Base(unittest.TestCase):
    files = {}
    docx = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="m4-")
        cls.root = os.path.join(cls.tmp, "proj")
        for rel, txt in cls.files.items():
            p = os.path.join(cls.root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(txt)
        if cls.docx:
            os.makedirs(cls.root, exist_ok=True)
            make_docx(os.path.join(cls.root, "report.docx"), cls.docx)
        cls.out = os.path.join(cls.tmp, "map")
        cls.d, _ = kit_map.build(cls.root, cls.out, run_gens=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def kinds(self, prefix):
        return [f for f in self.d["flags"] if f["kind"].startswith(prefix)]

    def found(self, faults):
        hits = [any(f["kind"] == k and w in f["text"] for f in self.d["flags"]) for k, w in faults]
        return sum(hits), hits

    def ids(self, kind):
        return {t["id"] for t in self.d["things"] if t["kind"] == kind}

    def link(self, frm, to, kind):
        return any(l["from"] == frm and l["to"] == to and l["kind"] == kind for l in self.d["links"])


class PowerBI(Base):
    files = PBI

    def test_bar_every_planted_fault_is_flagged(self):
        n, hits = self.found(PBI_FAULTS)
        print("\nBAR powerbi PBIP: %d of %d planted faults flagged" % (n, len(PBI_FAULTS)))
        self.assertEqual(n, len(PBI_FAULTS), hits)

    def test_no_false_flags_on_the_clean_parts(self):
        texts = " | ".join(f["text"] for f in self.kinds("pbi"))
        for clean in ("Total Sales", "Margin", "sql.corp.example", "sharepoint", "v1 ", "v2 "):
            self.assertNotIn(clean, texts)
        self.assertEqual(len(self.kinds("pbi")), len(PBI_FAULTS), texts)

    def test_things_cover_sources_tables_relationships_measures_pages_visuals_apps(self):
        self.assertEqual(self.ids("pbi-table"), {"pbi-table:%s" % t for t in ("Sales", "Customer", "Date", "Budget")})
        self.assertIn("pbi-measure:Total Sales", self.ids("pbi-measure"))
        self.assertIn("pbi-source:C:\\Users\\marie\\Desktop\\customers.csv", self.ids("pbi-source"))
        self.assertEqual(len(self.ids("pbi-relationship")), 4)
        self.assertEqual(len(self.ids("pbi-page")), 2)
        self.assertEqual(len(self.ids("pbi-visual")), 4)
        self.assertEqual(self.ids("pbi-app"), {"pbi-app:Sales app"})

    def test_links_visual_measure_table_source(self):
        self.assertTrue(self.link("pbi-visual:Sales/Overview/v1", "pbi-measure:Total Sales", "uses"))
        self.assertTrue(self.link("pbi-measure:Total Sales", "pbi-table:Sales", "reads"))
        self.assertTrue(self.link("pbi-measure:Margin", "pbi-measure:Total Sales", "uses"))
        self.assertTrue(self.link("pbi-table:Sales", "pbi-source:sql.corp.example", "loads"))
        self.assertTrue(self.link("pbi-app:Sales app", "pbi-report:Sales", "contains"))

    def test_overview_and_card_name_the_reader(self):
        ov = rd(os.path.join(self.out, "overview.md"))
        self.assertIn("Power BI: 3 sources, 4 tables", ov)
        self.assertLessEqual(ov.count("\n"), kit_map.MAX_OVERVIEW)
        self.assertNotIn("NOT READ", ov)
        self.assertIn("Profit YoY", rd(os.path.join(self.out, "areas", "powerbi.md")))

    def test_check_passes_fresh_then_sees_a_report_change_and_a_broken_link(self):
        rc, msg = kit_map.check(self.root, self.out, run_gens=False)
        self.assertEqual(rc, 0, msg)
        p = os.path.join(self.root, "Sales.Report/definition/pages/p1/visuals/v1/visual.json")
        old = rd(p)
        try:
            with open(p, "w") as fh:
                fh.write(old.replace("Total Sales", "Total Revenue"))
            rc, msg = kit_map.check(self.root, self.out, run_gens=False)
            self.assertEqual(rc, 1)
            self.assertIn("visual.json", msg)
            self.assertIn("Total Sales", msg)
        finally:
            with open(p, "w") as fh:
                fh.write(old)


class PowerBILegacy(Base):
    files = LEGACY

    def test_bar_model_bim_and_report_json(self):
        n, hits = self.found(LEGACY_FAULTS)
        print("\nBAR powerbi legacy: %d of %d planted faults flagged" % (n, len(LEGACY_FAULTS)))
        self.assertEqual(n, len(LEGACY_FAULTS), hits)
        self.assertTrue(self.link("pbi-visual:Ops/Main/c1", "pbi-measure:Hours Total", "uses"))
        self.assertFalse(any("Hours Total" in f["text"] for f in self.d["flags"]))


class PowerBIServiceOnly(Base):
    files = SERVICE_ONLY

    def test_says_what_is_needed_instead_of_mapping_nothing(self):
        fl = self.kinds("powerbi-needs-export")
        print("\nBAR powerbi service-only: %d of 1 planted fault flagged" % min(len(fl), 1))
        self.assertEqual(len(fl), 1)
        self.assertIn(".pbip", fl[0]["text"])
        self.assertIn("REST API", fl[0]["text"])
        self.assertIn("Finance.pbix", fl[0]["text"])
        self.assertIn("powerbi-needs-export", rd(os.path.join(self.out, "overview.md")))


class Thesis(Base):
    files = THESIS

    def test_bar_every_planted_fault_is_flagged(self):
        n, hits = self.found(THESIS_FAULTS)
        print("\nBAR documents thesis (LaTeX + Markdown): %d of %d planted faults flagged" % (n, len(THESIS_FAULTS)))
        self.assertEqual(n, len(THESIS_FAULTS), hits)

    def test_no_false_flags_on_the_clean_parts(self):
        texts = " | ".join(f["text"] for f in self.kinds("doc"))
        for clean in ("smith2020", "doe2019", "fig:design", "fig:map", "RQ1", "design.py"):
            self.assertNotIn(clean, texts)
        self.assertEqual(len(self.kinds("doc")), len(THESIS_FAULTS), texts)

    def test_sections_claims_figures_data_scripts_questions(self):
        self.assertIn("section:thesis.tex#Method", self.ids("section"))
        self.assertIn("section:chapters/results.tex#Results", self.ids("section"))
        claims = [t for t in self.d["things"] if t["kind"] == "claim"]
        self.assertTrue(any("effect is large" in c["text"] for c in claims))
        self.assertTrue(self.link("script:scripts/design.py", "figure:fig:design", "makes"))
        self.assertTrue(self.link("script:scripts/scores.py", "figure:fig:scores", "makes"))
        self.assertTrue(self.link("script:scripts/design.py", "data:data/schools.csv", "reads"))
        self.assertEqual(self.ids("question"), {"question:RQ1", "question:RQ2"})
        self.assertTrue(self.link("section:thesis.tex#Discussion", "question:RQ1", "answers"))
        self.assertTrue(self.link("doc:thesis.tex", "file:chapters/results.tex", "references"))

    def test_check_sees_a_document_change(self):
        rc, msg = kit_map.check(self.root, self.out, run_gens=False)
        self.assertEqual(rc, 0, msg)
        p = os.path.join(self.root, "notes.md")
        old = rd(p)
        try:
            with open(p, "w") as fh:
                fh.write(old + "\nMore.\n")
            rc, msg = kit_map.check(self.root, self.out, run_gens=False)
            self.assertEqual(rc, 1)
            self.assertIn("notes.md", msg)
        finally:
            with open(p, "w") as fh:
                fh.write(old)


class Word(Base):
    files = {"refs.bib": BIB}
    docx = WORD_PARAS

    def test_bar_word_document(self):
        n, hits = self.found(WORD_FAULTS)
        print("\nBAR documents Word: %d of %d planted faults flagged" % (n, len(WORD_FAULTS)))
        self.assertEqual(n, len(WORD_FAULTS), hits)
        self.assertEqual(len(self.kinds("doc")), len(WORD_FAULTS), [f["text"] for f in self.kinds("doc")])
        self.assertIn("section:report.docx#Findings", self.ids("section"))
        rc, msg = kit_map.check(self.root, self.out, run_gens=False)
        self.assertEqual(rc, 0, msg)


class Folder(Base):
    files = FOLDER

    def test_bar_big_and_broken(self):
        n, hits = self.found(FOLDER_FAULTS)
        print("\nBAR folder: %d of %d planted faults flagged" % (n, len(FOLDER_FAULTS)))
        self.assertEqual(n, len(FOLDER_FAULTS), hits)
        self.assertEqual(len(self.d["flags"]), len(FOLDER_FAULTS), [f["text"] for f in self.d["flags"]])

    def test_headings_links_references_sizes(self):
        self.assertTrue(self.link("doc:README.md", "file:plan.md", "references"))
        self.assertTrue(self.link("doc:README.md", "file:money/budget.csv", "references"))
        f = {t["id"]: t for t in self.d["things"]}
        self.assertEqual(f["file:money/budget.csv"]["bytes"], len(FOLDER["money/budget.csv"]))
        self.assertIn("section:plan.md#Q1", f)
        self.assertIn("folder:", rd(os.path.join(self.out, "overview.md")))


if __name__ == "__main__":
    unittest.main()
