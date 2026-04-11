---
name: honcho-upload-document
description: Upload documents to Honcho with automatic PDF text extraction using LiteParse. Streamlines document assimilation by extracting text from PDFs before storing.
skillLevel: intermediate
keywords: ["honcho", "upload", "document", "pdf", "liteparse", "memory", "knowledge"]
---

# Honcho Upload Document Skill

Upload documents to Honcho (agentic memory system) with automatic PDF text extraction. This skill streamlines document assimilation by:

1. **Detecting PDF files** automatically by extension
2. **Extracting text** using LiteParse (with OCR for scanned PDFs)
3. **Chunking intelligently** at paragraph boundaries
4. **Uploading to Honcho** as conclusions for semantic search

## Prerequisites

- Node.js >= 18
- `liteparse` CLI (`@llamaindex/liteparse` >= 0.9.0)
- Honcho server running (default: http://localhost:8300)
- Environment variables:
  - `HONCHO_BASE_URL` (optional, defaults to http://localhost:8300)
  - `HONCHO_WORKSPACE` (optional, auto-detected from git repo)
  - `HONCHO_USER` (optional, defaults to current user)

## Usage

### Basic Document Upload

```typescript
import { uploadDocument } from "./upload";

// Upload any document (PDFs auto-extracted, others read as-is)
const result = await uploadDocument({
  filePath: "~/Documents/paper.pdf",
  name: "Research Paper on AI",  // Optional, defaults to filename
  category: "AI Papers",
  subcategory: "Research"
});

console.log(`Uploaded as ${result.peerId} with ${result.chunkCount} chunks`);
```

### Upload with Content Directly

```typescript
import { uploadDocument } from "./upload";

// Upload raw content (skips file reading)
const result = await uploadDocument({
  content: "Raw text content here...",
  name: "My Notes",
  category: "Notes"
});
```

### Upload Non-PDF Files

```typescript
// Text files are read directly without LiteParse
const result = await uploadDocument({
  filePath: "/path/to/document.txt",
  name: "Configuration Notes"
});
```

## API Reference

### `uploadDocument(options): Promise<UploadResult>`

Uploads a document to Honcho, with automatic PDF text extraction.

**Parameters:**

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `filePath` | `string` | *if no content* | Path to document file |
| `content` | `string` | *if no filePath* | Raw text content |
| `name` | `string` | No | Document name (defaults to filename or "untitled") |
| `category` | `string` | No | Category for organization (e.g., "AI Papers") |
| `subcategory` | `string` | No | Subcategory for organization |
| `observerId` | `string` | No | Peer ID making observation (defaults to env or "agent-pi-mono") |
| `workspace` | `string` | No | Honcho workspace (defaults to env or auto-detected) |

**Returns:**

| Property | Type | Description |
|----------|------|-------------|
| `peerId` | `string` | Document peer ID in Honcho |
| `chunkCount` | `number` | Number of conclusions created |
| `conclusionIds` | `string[]` | IDs of created conclusions |
| `sessionId` | `string` | Session used for upload |

## How It Works

### PDF Detection & Extraction

```
1. Check file extension (.pdf, case-insensitive)
2. YES → Run LiteParse with OCR
   └─ Handles both text-selectable and scanned PDFs
3. NO → Read text file directly
4. Chunk at paragraph boundaries
5. Upload each chunk as Honcho conclusion
```

### Chunking Strategy

Documents are split intelligently:

- **Paragraph boundaries** (double newlines) preferred
- **Chapter headings** preserved with context
- **Max chunk size** ~4000 chars (configurable)
- **Large paragraphs** split at sentence boundaries

### Honcho Storage Model

```
Workspace (local-honcho)
└── Peer: "Research-Paper-on-AI"  (the document)
    └── Session: "doc-upload-..."  
        └── Conclusions[0]: "[Part 1/3 | Chapter 1] ..."
        └── Conclusions[1]: "[Part 2/3 | Chapter 2] ..."
        └── Conclusions[2]: "[Part 3/3 | Conclusion] ..."
```

Each conclusion:
- `observer_id`: agent-pi-mono (who observed)
- `observed_id`: document peer ID (what was observed)
- `content`: chunk text with part marker

## Error Handling

Common errors and solutions:

```typescript
try {
  const result = await uploadDocument({ filePath: "doc.pdf" });
} catch (error) {
  if (error.message.includes("ENOENT")) {
    // File not found - check path
  } else if (error.message.includes("404")) {
    // Honcho workspace/peer not found
  } else if (error.message.includes("LiteParse")) {
    // PDF extraction failed - may be corrupted
  }
}
```

## Examples

### Upload PDF from Papers Folder

```typescript
const result = await uploadDocument({
  filePath: "~/Documents/AI Papers/transformer.pdf",
  name: "Attention Is All You Need",
  category: "AI Papers",
  subcategory: "NLP"
});
// → Extracted 6 pages, chunked into 4 conclusions
```

### Upload Code Documentation

```typescript
const result = await uploadDocument({
  filePath: "./API.md",
  name: "Project API Documentation",
  category: "Documentation"
});
// → Read as text, chunked into 2 conclusions
```

### Batch Upload Multiple PDFs

```typescript
const pdfs = ["paper1.pdf", "paper2.pdf", "paper3.pdf"];

for (const pdf of pdfs) {
  try {
    const result = await uploadDocument({ filePath: pdf });
    console.log(`✓ ${pdf}: ${result.chunkCount} chunks`);
  } catch (e) {
    console.error(`✗ ${pdf}: ${e.message}`);
  }
}
```

## Performance

| Operation | Typical Time | Notes |
|-----------|--------------|-------|
| PDF Detection | <1ms | Extension check only |
| LiteParse OCR | 1-5s | Depends on PDF size & pages |
| LiteParse no-OCR | 100-500ms | For text-selectable PDFs |
| Text file read | 10-100ms | Depends on file size |
| Honcho upload | 200-500ms | Per conclusion |

**Tips:**
- Use `--no-ocr` equivalent for text PDFs (not yet exposed, planned)
- Batch uploads to amortize session creation cost
- Pre-extract large PDFs if doing multiple uploads

## Troubleshooting

### "LiteParse not found"

Install the CLI:
```bash
npm install -g @llamaindex/liteparse
```

### "Honcho API error: 404"

- Check Honcho server is running on port 8300
- Verify workspace exists (auto-created on first use)
- Check `HONCHO_BASE_URL` env var

### PDF extracts to garbage text

- PDF may be image-based scans - LiteParse OCR handles this
- Try without `--no-ocr` (OCR enabled by default for PDFs)
- Check PDF isn't corrupted: `pdfinfo document.pdf`

### Chunks too large

Default max chunk is 4000 chars. To customize, edit:
```typescript
const MAX_CHUNK_SIZE = 4000; // In upload.ts
```

## Related Skills

- **`liteparse`** - Lower-level PDF parsing skill
- **`honcho-apply-learnings`** - Apply Honcho insights to agent prompts
- **`agent-memory`** - Use Honcho for agent context management

## Links

- [Honcho Documentation](https://github.com/mariozechner/honcho)
- [LiteParse GitHub](https://github.com/run-llama/liteparse)
- [Skill Source](./upload.ts)