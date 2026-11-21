"""Read only the rows and columns a tool needs from one table file (long Parquet), in file order, a row group at a time.

Some tables have over 100 million rows, so no tool reads a whole table. The tools used to compare and print values
as pandas shows the whole table (`df[col].astype(str)`); TableFile keeps that meaning: `equals` and `texts` treat a
value as the text pandas would make of it. A missing value (null, NaN) never matches any text and never becomes part
of a coordinate, whatever pandas version is installed (pandas 2 turned it into the text "None" or "nan" there;
show_table.ref_of, too, leaves a coordinate with a missing part NaN, as pandas 3 does). An integer column with a
null anywhere in the file is float64 in pandas ("2023.0"), so that is looked up in the file footer.
Used by scripts/show_table.py and evals/check_items.py (which puts this folder on its path).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

BATCH = 1 << 20  # rows per batch; the files have 500k-row groups, so a batch is at most one row group
NOTHING = ds.scalar(False)
OR_CHUNK = 200  # arrow recurses over a long OR to prune row groups: a small C stack (Windows: 1 MB) overflows


class TableFile:
    def __init__(self, path: Path):
        self.path = path
        self.file = pq.ParquetFile(path)
        self.schema = self.file.schema_arrow
        self.rows = self.file.metadata.num_rows
        self.dataset = ds.dataset(path, format="parquet")
        self._floats: dict[str, bool] = {}

    def _stats(self, name: str):
        meta = self.file.metadata
        index = next(i for i in range(meta.num_columns) if meta.schema.column(i).path == name)
        groups = [meta.row_group(g) for g in range(meta.num_row_groups)]
        return [(group.num_rows, group.column(index).statistics) for group in groups]

    def all_null(self, name: str) -> bool:
        """True only when the footer says every value is null (no statistics: False, so the column is read)."""
        stats = self._stats(name)
        return all(s is not None and s.has_null_count and s.null_count == n for n, s in stats)

    def int_as_float(self, name: str) -> bool:
        """An integer column with a null becomes float64 in pandas, so 2023 reads "2023.0"."""
        if name not in self._floats:
            kind = self.schema.field(name).type
            nulls = False
            if pa.types.is_integer(kind):
                stats = self._stats(name)
                if all(s is not None and s.has_null_count for _, s in stats):
                    nulls = sum(s.null_count for _, s in stats) > 0
                else:
                    nulls = self.count(ds.field(name).is_null()) > 0
            self._floats[name] = nulls
        return self._floats[name]

    def _kind(self, name: str):
        kind = self.schema.field(name).type
        return kind.value_type if pa.types.is_dictionary(kind) else kind

    def equals(self, name: str, text: str) -> ds.Expression:
        """Rows whose value pandas would print as `text` (`df[name].astype(str) == text`)."""
        kind, field = self._kind(name), ds.field(name)
        if pa.types.is_dictionary(self.schema.field(name).type):
            field = field.cast(kind)
        if pa.types.is_string(kind) or pa.types.is_large_string(kind):
            return field == text
        if pa.types.is_integer(kind) and not self.int_as_float(name):
            try:
                n = int(text)
            except ValueError:
                return NOTHING
            info = np.iinfo(kind.to_pandas_dtype())
            return field == pa.scalar(n, kind) if str(n) == text and info.min <= n <= info.max else NOTHING
        if pa.types.is_integer(kind) or pa.types.is_floating(kind):
            number = np.float64 if pa.types.is_integer(kind) else kind.to_pandas_dtype()
            try:
                x = number(float(text))
            except ValueError:
                return NOTHING
            if pa.types.is_integer(kind):  # else values beyond 2**24 fail (beyond 2**53: still)
                field = field.cast(pa.float64(), safe=False)
            return field == float(x) if x == x and str(x) == text else NOTHING  # NaN is missing: never equal
        if pa.types.is_boolean(kind):
            return field == (text == "True") if text in ("True", "False") else NOTHING
        return field.cast(pa.string()) == text  # other types do not occur in the tables; close enough

    def texts(self, name: str, values) -> pa.Array:
        """The text pandas makes of each value; null where pandas has a missing value."""
        if isinstance(values, pa.ChunkedArray):
            values = values.combine_chunks()
        if pa.types.is_dictionary(values.type):
            values = values.dictionary_decode()
        kind = values.type
        if pa.types.is_string(kind):
            return values
        if pa.types.is_large_string(kind) or (pa.types.is_integer(kind) and not self.int_as_float(name)):
            return values.cast(pa.string())
        number = np.float64 if pa.types.is_integer(kind) else None
        out = [None if v is None or v != v else str(number(v) if number else v)
               for v in values.to_numpy(zero_copy_only=False)]
        return pa.array(out, pa.string())

    def to_pandas(self, table: pa.Table):
        """A slice as pandas, with the column types pandas gives the whole table."""
        for i, name in enumerate(table.column_names):
            kind, file_kind = table[name].type, self.schema.field(name).type
            if pa.types.is_dictionary(kind) and not pa.types.is_dictionary(file_kind):
                table = table.set_column(i, name, table[name].cast(kind.value_type))  # read as dictionary by `read`
            elif pa.types.is_dictionary(file_kind) and not pa.types.is_dictionary(kind):  # pandas: categorical
                table = table.set_column(i, name, table[name].dictionary_encode().cast(file_kind))
            if self.int_as_float(name):
                table = table.set_column(i, name, table[name].cast(pa.float64(), safe=False))  # rounded, as pandas
        return table.to_pandas()

    def row_groups_any(self, terms: list[ds.Expression]) -> list[int]:
        """The row groups the footer does not rule out for at least one of the terms (their OR), OR_CHUNK terms at a
        time so that no expression is deep: any number of terms is safe."""
        import operator
        from functools import reduce

        return sorted({g for i in range(0, len(terms), OR_CHUNK)
                       for g in self.row_groups(reduce(operator.or_, terms[i:i + OR_CHUNK], NOTHING))})

    def row_groups(self, where: ds.Expression | None = None) -> list[int]:
        """The row groups the footer statistics do not rule out."""
        whole = next(self.dataset.get_fragments())
        try:
            parts = whole.split_by_row_group(where) if where is not None else whole.split_by_row_group()
        except pa.ArrowInvalid:  # integer statistics beyond 2**53 cannot be compared with a float: rule out none
            parts = whole.split_by_row_group()
        return [part.row_groups[0].id for part in parts]

    def read(self, group: int, columns: list[str]) -> pa.Table:
        """One row group; text columns come as dictionaries, several times faster than reading every text."""
        if not hasattr(self, "_dict_file"):
            texts = [f.name for f in self.schema if pa.types.is_string(f.type)]
            self._dict_file = pq.ParquetFile(self.path, read_dictionary=texts)
        return self._dict_file.read_row_group(group, columns=columns)

    def batches(self, columns: list[str], where: ds.Expression | None = None):
        """Record batches of the rows that match, in file order; row groups the footer rules out are not read."""
        whole = next(self.dataset.get_fragments())
        parts = whole.split_by_row_group(where) if where is not None else whole.split_by_row_group()
        for part in parts:
            for batch in part.to_batches(columns=columns, filter=where, batch_size=BATCH):
                if batch.num_rows:
                    yield batch

    def count(self, where: ds.Expression) -> int:
        """Matching rows, reading only the columns `where` uses."""
        return self.dataset.count_rows(filter=where)


def ref_column(tf: TableFile, batch: pa.RecordBatch) -> pa.Array:
    """Each row's coordinate tbl_id|item_id|<code of each category the row has>|period, as pandas built it.
    A missing code is skipped; a missing tbl_id, item_id or period makes the whole coordinate null."""
    names = batch.schema.names
    ref = pc.binary_join_element_wise(tf.texts("tbl_id", batch["tbl_id"]), tf.texts("item_id", batch["item_id"]), "|")
    for i in range(1, 9):
        code = f"dim{i}_code"
        if code in names:
            part = pc.fill_null(pc.binary_join_element_wise("", tf.texts(code, batch[code]), "|"), "")
            ref = pc.binary_join_element_wise(ref, part, "")
    return pc.binary_join_element_wise(ref, tf.texts("period", batch["period"]), "|")
