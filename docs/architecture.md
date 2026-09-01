# Wixq data model

The crawler has one authority chain:

```text
authenticated API request -> raw_pages -> posts -> JSONL / Markdown
```

Each page records its request metadata, status, fetch timestamp, complete JSON
response, and the cursor selected for the following page. Existing valid raw
pages are immutable. On startup Wixq validates page numbering and cursor links,
then rebuilds derived posts before it trusts the checkpoint file.

The group endpoint is read from configuration, with the tested default:

```text
/v2/groups/{group_id}/topics
count=20
end_time=<cursor>
```

Wixq saves a topic only when it has text or an image URL. Attachment-only,
audio-only, and video-only topics are ignored without downloading their files.
