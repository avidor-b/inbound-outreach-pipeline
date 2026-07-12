"""Unit tests for index_materials.py"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from outreach_pipeline.index_materials import read_file, index_materials


class TestTextSplitter(unittest.TestCase):
    def test_short_text_single_chunk(self):
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20)
        chunks = splitter.split_text("Hello world")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world")

    def test_splits_long_text(self):
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        text = "A " * 600  # ~1200 chars
        chunks = splitter.split_text(text)
        self.assertTrue(len(chunks) >= 2)

    def test_respects_sentence_boundaries(self):
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=50, chunk_overlap=10,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        text = "First sentence here. Second sentence here. Third sentence here."
        chunks = splitter.split_text(text)
        # Chunks should break at sentence boundaries, not mid-word
        for chunk in chunks:
            self.assertFalse(chunk.startswith(" "))


class TestReadFile(unittest.TestCase):
    def test_reads_text_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            self.assertEqual(read_file(f.name), "test content")
            os.unlink(f.name)

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(read_file("/nonexistent/file.txt"))


class TestIndexMaterials(unittest.TestCase):
    def test_indexes_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "product.txt").write_text("Our product helps companies automate workflows.")
            (mat_dir / "case_study.md").write_text("# Case Study\nAcme Corp reduced costs by 40%.")

            db_path = os.path.join(tmpdir, "db")
            result = index_materials(str(mat_dir), db_path)
            self.assertTrue(result["success"])
            self.assertEqual(result["files_indexed"], 2)
            self.assertTrue(result["chunks_indexed"] >= 2)

    def test_skips_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "image.png").write_bytes(b"\x89PNG")
            (mat_dir / "doc.txt").write_text("Valid content")

            db_path = os.path.join(tmpdir, "db")
            result = index_materials(str(mat_dir), db_path)
            self.assertEqual(result["files_indexed"], 1)

    def test_handles_missing_directory(self):
        result = index_materials("/nonexistent/dir", "/tmp/db_test")
        self.assertFalse(result["success"])

    def test_reset_clears_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "a.txt").write_text("Content A")

            db_path = os.path.join(tmpdir, "db")
            index_materials(str(mat_dir), db_path)
            result = index_materials(str(mat_dir), db_path, reset=True)
            self.assertTrue(result["success"])
            self.assertEqual(result["files_indexed"], 1)

    def test_indexes_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            sub = mat_dir / "case_studies"
            sub.mkdir(parents=True)
            (sub / "acme.txt").write_text("Acme case study content")

            db_path = os.path.join(tmpdir, "db")
            result = index_materials(str(mat_dir), db_path)
            self.assertEqual(result["files_indexed"], 1)


class TestCollectionName(unittest.TestCase):
    def test_custom_collection_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mat_dir = Path(tmpdir) / "materials"
            mat_dir.mkdir()
            (mat_dir / "doc.txt").write_text("Some content here for testing")
            db_path = os.path.join(tmpdir, "db")
            result = index_materials(str(mat_dir), db_path, collection_name="custom_col")
            self.assertTrue(result["success"])
            # Verify the collection exists with the custom name
            import chromadb
            client = chromadb.PersistentClient(path=db_path)
            col = client.get_collection("custom_col")
            self.assertTrue(col.count() > 0)

    def test_separate_collections_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = Path(tmpdir) / "a"
            dir_b = Path(tmpdir) / "b"
            dir_a.mkdir()
            dir_b.mkdir()
            (dir_a / "a.txt").write_text("Content about apples and fruit")
            (dir_b / "b.txt").write_text("Content about cars and engines")
            db_path = os.path.join(tmpdir, "db")
            index_materials(str(dir_a), db_path, collection_name="col_a")
            index_materials(str(dir_b), db_path, collection_name="col_b")
            import chromadb
            client = chromadb.PersistentClient(path=db_path)
            self.assertTrue(client.get_collection("col_a").count() > 0)
            self.assertTrue(client.get_collection("col_b").count() > 0)

    def test_parse_args_collection(self):
        from outreach_pipeline.index_materials import parse_args
        args = parse_args(["--collection", "my_collection"])
        self.assertEqual(args.collection, "my_collection")


if __name__ == "__main__":
    unittest.main()
