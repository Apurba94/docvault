# DocVault

Search everything on your computer, offline. DocVault reads the text inside Word, Excel, PDF and text files, builds an index, and also finds duplicate files and old versions. Your files stay where they are, and it never changes or deletes them.

```
python -m docvault
```

Then open http://127.0.0.1:8042 and add a folder. It needs Python 3.10 or newer, and nothing else.

## What it does

- **Reads** `.docx`, `.xlsx`, `.pdf`, `.csv`, `.txt`, `.md` and other text files
- **Searches** words, `"exact phrases"`, `OR`, `NOT` and prefixes such as `rep*`, in English, Bangla and other languages, with highlighted text
- **Updates quickly:** unchanged files are skipped, changed files are read again, and deleted ones leave the index
- **Finds exact duplicates,** with the space you could recover
- **Finds likely versions:** drafts with mostly the same words, using MinHash sketches
- **Is honest about gaps:** the "Unread files" page lists what could not be read, so that a search with no results does not give false confidence

## About PDFs

The PDF reader handles many office-made PDFs. Scanned pages have no text to find, and PDFs that use custom font encodings, as many Bangla ones do, often give nothing or garbled text. Those are listed under Unread files when almost no text comes out, but garbled text may pass unnoticed. For these, run OCR first, or convert them to Word.

## How it is tested

Tests build real Word, Excel and PDF bytes and read them back, including Bangla text and broken files that must not cause a crash. Search is tested for each query form and for punctuation that could otherwise break the query. The similarity sketch is compared with the true overlap on generated text, and a test confirms that indexing leaves files untouched.

## Limits

It does not do OCR, or read old `.doc` files, images or email. Word forms such as plurals are not merged, so search for both. Version checks compare every pair, so they slow down on tens of thousands of files. The index contains your documents' text, so keep it as private as the documents themselves.

## Tests

```
python -m unittest discover -s tests
```

MIT licence.
