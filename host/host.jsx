var Minecut = function() {
    function encode(v) {
        if (v === null || v === undefined) return "null";
        if (typeof v === "string") return '"' + v.replace(/[\\"\x00-\x1f\u2028\u2029]/g, function(c) {
            var n = c.charCodeAt(0).toString(16);
            return "\\u" + ("0000" + n).slice(-4);
        }) + '"';
        if (typeof v === "boolean") return v ? "true" : "false";
        if (typeof v === "number") return isFinite(v) ? String(v) : "null";
        var values = [], k;
        if (v instanceof Array) {
            for (k = 0; k < v.length; k++) values.push(encode(v[k]));
            return "[" + values.join(",") + "]";
        }
        for (k in v) if (v.hasOwnProperty(k)) values.push(encode(k) + ":" + encode(v[k]));
        return "{" + values.join(",") + "}";
    }
    function reply(fn) {
        try {
            return encode(fn());
        } catch (e) {
            return encode({
                ok: false,
                error: String(e)
            });
        }
    }
    function fileInfo(item) {
        if (!item || !item.getMediaPath) throw Error("Choose one source video, not a bin or nested sequence.");
        if (item.isSequence && item.isSequence()) throw Error("Choose original source footage. Nested/composited sequences are not supported.");
        var path = item.getMediaPath();
        if (!path || !new File(path).exists) throw Error("Source media is offline. Relink it first.");
        return {
            ok: true,
            mediaPath: path,
            name: item.name,
            scope: "Entire original source file"
        };
    }
    return {
        projectInfo: function() {
            return reply(function() {
                return {
                    ok: true,
                    path: app.project.path || "",
                    name: app.project.name
                };
            });
        },
        browse: function() {
            return reply(function() {
                var f = File.openDialog("Choose Minecraft source video");
                return f ? {
                    ok: true,
                    mediaPath: f.fsName,
                    name: f.name,
                    scope: "Entire source file"
                } : {
                    ok: false,
                    cancelled: true
                };
            });
        },
        selectedSource: function() {
            return reply(function() {
                var seq = app.project.activeSequence, selected = seq && seq.getSelection ? seq.getSelection() : [], videos = [];
                for (var i = 0; i < selected.length; i++) if (selected[i].mediaType === "Video") videos.push(selected[i]);
                if (videos.length > 1) throw Error("Select exactly one timeline video clip. Linked audio is allowed.");
                if (videos.length === 1) {
                    var clip = videos[0];
                    if (clip.getSpeed && Math.abs(clip.getSpeed() - 1) > 1e-6) throw Error("Choose a clip at normal speed; speed changes are not supported.");
                    if (clip.isSpeedReversed && clip.isSpeedReversed()) throw Error("Reversed clips are not supported.");
                    var r = fileInfo(clip.projectItem);
                    r.sourceRange = {
                        start_seconds: Number(clip.inPoint.seconds),
                        end_seconds: Number(clip.outPoint.seconds)
                    };
                    if (!isFinite(r.sourceRange.start_seconds) || !isFinite(r.sourceRange.end_seconds) || r.sourceRange.end_seconds <= r.sourceRange.start_seconds) throw Error("The selected clip has invalid source in/out points.");
                    r.scope = "Selected clip: " + r.sourceRange.start_seconds.toFixed(2) + " - " + r.sourceRange.end_seconds.toFixed(2) + " s of source";
                    return r;
                }
                var items = app.getCurrentProjectViewSelection ? app.getCurrentProjectViewSelection() : [];
                if (items && items.length === 1) return fileInfo(items[0]);
                throw Error("Select one video clip in the timeline or one source in the Project panel. Or use Browse.");
            });
        },
        importEdit: function(a) {
            return reply(function() {
                if (String(app.project.path || "") !== a.projectPath) throw Error("The open project changed. Try Import again.");
                if (!app.project.path) throw Error("Save your Premiere project before importing.");
                if (!new File(a.path).exists) throw Error("The cut XML is missing. Build the sequence again.");
                var ids = {}, i;
                for (i = 0; i < app.project.sequences.numSequences; i++) ids[app.project.sequences[i].sequenceID] = true;
                if (!app.project.importFiles([ a.path ], true, app.project.rootItem, false)) throw Error("Premiere rejected the XML import.");
                var added = [];
                for (i = 0; i < app.project.sequences.numSequences; i++) {
                    var seq = app.project.sequences[i];
                    if (!ids[seq.sequenceID]) added.push(seq);
                }
                if (added.length !== 1) throw Error("Import outcome needs inspection: expected one new sequence. Do not retry blindly.");
                var s = added[0], count = 0;
                for (i = 0; i < s.videoTracks.numTracks; i++) count += s.videoTracks[i].clips.numItems;
                if (count !== a.clips) throw Error("Imported sequence has " + count + " clips; expected " + a.clips + ". Inspect it before retrying.");
                if (a.openAfter) app.project.openSequence(s.sequenceID);
                app.project.save();
                return {
                    ok: true,
                    name: s.name,
                    id: s.sequenceID,
                    clips: count
                };
            });
        }
    };
}();