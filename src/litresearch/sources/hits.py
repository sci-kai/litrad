"""One search result from a literature database (Europe PMC, PubMed)."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Hit:
    source: str  # "europepmc" | "pubmed"
    title: str
    pmid: str | None = None
    pmcid: str | None = None  # "PMC1234567"
    doi: str | None = None
    authors: list[str] = field(default_factory=list)  # "Surname Initials"
    journal: str | None = None
    year: int | None = None
    pub_date: date | None = None
    abstract: str | None = None
    cited_by: int | None = None
    open_access: bool | None = None
    pub_types: list[str] = field(default_factory=list)
    # every database that returned this paper (after merging)
    found_in: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Identity for merging results of several databases."""
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        return f"title:{self.title.lower()}"

    @property
    def ref(self) -> str | None:
        """How to refer to the paper when adding it (DOI, else PMID, else PMC id)."""
        if self.doi:
            return self.doi
        if self.pmid:
            return f"pmid:{self.pmid}"
        return self.pmcid

    @property
    def url(self) -> str | None:
        if self.pmcid:
            return f"https://europepmc.org/article/PMC/{self.pmcid}"
        if self.pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"
        if self.doi:
            return f"https://doi.org/{self.doi}"
        return None

    def merge(self, other: "Hit") -> None:
        """Fill gaps with the data of the same paper from another database."""
        for name in ("pmid", "pmcid", "doi", "journal", "year", "pub_date", "abstract"):
            if getattr(self, name) in (None, "") and getattr(other, name):
                setattr(self, name, getattr(other, name))
        if self.cited_by is None:
            self.cited_by = other.cited_by
        if self.open_access is None:
            self.open_access = other.open_access
        self.authors = self.authors or other.authors
        self.pub_types = self.pub_types or other.pub_types
        for source in other.found_in:
            if source not in self.found_in:
                self.found_in.append(source)
