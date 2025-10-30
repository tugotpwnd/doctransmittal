# widgets/filter_proxy.py
from PyQt5.QtCore import QSortFilterProxyModel, Qt

class RegisterFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._search = ""
        self._statuses = set()
        self._only_selected = False
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)

    def set_search_text(self, text: str):
        self._search = (text or "").strip()
        self.invalidateFilter()

    def set_statuses(self, statuses):
        self._statuses = {s for s in (statuses or []) if s}
        self.invalidateFilter()

    def set_only_selected(self, on: bool):
        self._only_selected = bool(on)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        m = self.sourceModel()
        cols = m.columnCount()
        blob = []
        for c in range(cols):
            v = m.data(m.index(source_row, c, source_parent), Qt.DisplayRole)
            blob.append("" if v is None else str(v))
        blob = " ".join(blob).lower()

        if self._search and self._search.lower() not in blob:
            return False
        if self._statuses and not any(s.lower() in blob for s in self._statuses):
            return False

        if self._only_selected:
            st = m.data(m.index(source_row, 0, source_parent), Qt.CheckStateRole)
            if st != Qt.Checked:
                return False

        return True
