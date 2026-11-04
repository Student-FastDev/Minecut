(function() {
    "use strict";
    var $ = function(id) {
        return document.getElementById(id);
    };
    var cats = [ "ore", "stone", "soil", "wood", "foliage", "constructed", "glass", "other" ];
    var labels = [ "Ore", "Stone", "Soil", "Wood", "Leaves", "Built", "Glass", "Other" ];
    var cs = new CSInterface, source = null, sourceRange = null, sourceMode = null, cached = null, result = null, active = null, busy = false, started = 0, timer = null, cancelled = false;
    var nodeRequire = window.cep_node ? window.cep_node.require : typeof require === "function" ? require : null;
    var fs, path, cp, os, root, work, sourceRevision = 0, selectionPending = false;
    if (nodeRequire) {
        fs = nodeRequire("fs");
        path = nodeRequire("path");
        cp = nodeRequire("child_process");
        os = nodeRequire("os");
        var here = decodeURIComponent(window.location.pathname);
        if (os.platform() === "win32" && /^\/[A-Za-z]:/.test(here)) here = here.slice(1);
        root = path.resolve(path.dirname(here), "..");
        work = path.join(os.platform() === "win32" ? nodeRequire("process").env.LOCALAPPDATA || os.homedir() : path.join(os.homedir(), "Library", "Caches"), "Minecut");
    }
    function sentencePunctuation(text) {
        text = String(text || "").trim();
        text = text.replace(/;\s*([a-z])/g, function(_, letter) {
            return ". " + letter.toUpperCase();
        });
        return text && !/[.!?...]$/.test(text) ? text + "." : text;
    }
    function showError(e) {
        var box = $("error"), message = sentencePunctuation(e.message || String(e)).replace(/^Error[:.]\s*/, "");
        box.textContent = "";
        var title = document.createElement("strong");
        title.textContent = "Error";
        box.appendChild(title);
        box.appendChild(document.createTextNode(". " + message));
        box.classList.remove("hidden");
    }
    function clearError() {
        $("error").classList.add("hidden");
    }
    function host(method, arg) {
        return new Promise(function(resolve, reject) {
            if (!window.__adobe_cep__) return reject(Error("Open this panel inside Adobe Premiere Pro."));
            var literal = arg === undefined ? "" : JSON.stringify(arg).replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
            var call = "Minecut." + method + "(" + literal + ")";
            cs.evalScript(call, function(raw) {
                try {
                    var r = JSON.parse(raw);
                    if (!r.ok && !r.cancelled) throw Error(r.error || "Premiere operation failed");
                    resolve(r);
                } catch (e) {
                    reject(e);
                }
            });
        });
    }
    function settings() {
        return {
            action: document.querySelector("#actions .active").dataset.value,
            categories: Array.from(document.querySelectorAll('.chip[aria-pressed="true"]')).map(function(b) {
                return b.dataset.value;
            }),
            unit: $("unit").value,
            before: Number($("before").value),
            after: Number($("after").value),
            duplicates: $("duplicates").checked ? "one" : "all",
            every: Number($("every").value),
            offset: Number($("startEvent").value) - 1,
            max_clips: Number($("maxClips").value),
            max_seconds: Number($("maxSeconds").value),
            limit_mode: $("limitMode").value,
            duplicate_winner: $("duplicateWinner").value,
            min_gap: Number($("minGap").value),
            order: $("cutOrder").value,
            max_audio: $("maxAudio").checked,
            peak_before: Number($("peakBefore").value),
            peak_after: Number($("peakAfter").value),
            peak_stream: Number($("peakStream").value),
            peak_channel: $("peakChannel").value,
            peak_floor_db: Number($("peakFloor").value),
            quiet_policy: $("quietPolicy").value,
            peak_clamp: $("peakClamp").checked,
            name: $("sequenceName").value.trim()
        };
    }
    function setAction(a) {
        document.querySelectorAll("#actions button").forEach(function(b) {
            var selected = b.dataset.value === a;
            b.classList.toggle("active", selected);
            b.setAttribute("aria-pressed", String(selected));
        });
    }
    function markChanged() {
        result = null;
        $("result").classList.add("hidden");
        $("runLabel").textContent = cached ? "Rebuild cut" : "Analyze video";
        $("cacheHint").textContent = cached ? "Change your cut without analyzing the video again." : "Runs locally. Your video stays here.";
        var first = Number($("startEvent").value) || 1, step = Number($("every").value) || 1;
        $("sampleHint").textContent = step === 1 && first === 1 ? "Every matching interaction." : "Events " + first + ", " + (first + step) + ", " + (first + 2 * step) + "...";
    }
    function apply(o) {
        setAction(o.action || "break");
        document.querySelectorAll(".chip").forEach(function(b) {
            b.setAttribute("aria-pressed", String((o.categories || cats).indexOf(b.dataset.value) >= 0));
        });
        $("unit").value = o.unit || "frames";
        $("before").value = o.before === undefined ? 3 : o.before;
        $("after").value = o.after === undefined ? 6 : o.after;
        $("duplicates").checked = o.duplicates !== "all";
        $("every").value = o.every || 1;
        $("sequenceName").value = o.name || "";
        $("startEvent").value = (o.offset || 0) + 1;
        $("maxClips").value = o.max_clips || 0;
        $("maxSeconds").value = o.max_seconds || 0;
        $("limitMode").value = o.limit_mode || "first";
        $("duplicateWinner").value = o.duplicate_winner || "confidence";
        $("minGap").value = o.min_gap || 0;
        $("cutOrder").value = o.order || "chronological";
        $("maxAudio").checked = !!o.max_audio;
        $("peakBefore").value = o.peak_before === undefined ? 3 : o.peak_before;
        $("peakAfter").value = o.peak_after === undefined ? 6 : o.peak_after;
        var trackValue = String(o.peak_stream || 0);
        if (!Array.from($("peakStream").options).some(function(x) {
            return x.value === trackValue;
        })) {
            var trackOption = document.createElement("option");
            trackOption.value = trackValue;
            trackOption.textContent = "Track " + (Number(trackValue) + 1);
            $("peakStream").appendChild(trackOption);
        }
        $("peakStream").value = trackValue;
        $("peakChannel").value = o.peak_channel || "loudest";
        $("peakFloor").value = o.peak_floor_db === undefined ? -60 : o.peak_floor_db;
        $("quietPolicy").value = o.quiet_policy || "keep";
        $("peakClamp").checked = !!o.peak_clamp;
        advancedUI();
        unitUI();
        markChanged();
    }
    function advancedUI() {
        $("peakSettings").classList.toggle("hidden", !$("maxAudio").checked);
    }
    function unitUI() {
        var sec = $("unit").value === "seconds";
        document.querySelectorAll(".unit-label").forEach(function(e) {
            e.textContent = sec ? "sec" : "fr";
        });
        [ "before", "after" ].forEach(function(id) {
            $(id).step = sec ? ".01" : "1";
            $(id).max = sec ? "60" : "3600";
        });
    }
    function setSource(r) {
        if (r.cancelled) return;
        var changed = source !== r.mediaPath || JSON.stringify(sourceRange) !== JSON.stringify(r.sourceRange || null);
        source = r.mediaPath;
        sourceRange = r.sourceRange || null;
        sourceMode = sourceRange ? "timeline" : "file";
        if (changed) {
            cached = null;
            result = null;
        }
        $("sourceName").textContent = r.name || source.split(/[\\/]/).pop();
        $("sourceName").title = source;
        $("sourceScope").textContent = sentencePunctuation(r.scope || "Entire original source file");
        $("sourceBadge").textContent = "READY";
        markChanged();
        clearError();
        if (!changed) return;
        var revision = ++sourceRevision;
        invoke({
            command: "media_info",
            source: source,
            source_range: sourceRange
        }, null, false).then(function(info) {
            if (sourceRevision !== revision) return;
            var previous = $("peakStream").value;
            $("peakStream").textContent = "";
            (info.audio_tracks.length ? info.audio_tracks : [ {
                index: 0,
                name: "No audio - quiet policy applies",
                channels: 0
            } ]).forEach(function(t) {
                var opt = document.createElement("option");
                opt.value = t.index;
                opt.textContent = "Track " + (t.index + 1) + " - " + t.name + " (" + t.channels + " ch)";
                $("peakStream").appendChild(opt);
            });
            $("peakStream").value = Array.from($("peakStream").options).some(function(o) {
                return o.value === previous;
            }) ? previous : "0";
            if (!busy) {
                cached = info.events_path || null;
                markChanged();
            }
        }).catch(function(e) {
            if (sourceRevision === revision) showError(e);
        });
    }
    function refreshSelection() {
        if (busy || selectionPending || sourceMode !== "timeline" || !window.__adobe_cep__ || document.hidden) return;
        selectionPending = true;
        host("selectedSource").then(function(r) {
            if (!busy && (source !== r.mediaPath || JSON.stringify(sourceRange) !== JSON.stringify(r.sourceRange || null))) setSource(r);
        }).catch(function() {
            if (!busy && cached) {
                cached = null;
                markChanged();
            }
        }).then(function() {
            selectionPending = false;
        });
    }
    function runtimeCommand() {
        var exe = path.join(root, "runtime", "minecut-worker", os.platform() === "win32" ? "minecut-worker.exe" : "minecut-worker");
        if (fs.existsSync(exe)) return {
            exe: exe,
            args: [ "--root", root ]
        };
        var env = nodeRequire("process").env;
        var python = $("pythonPath").value.trim() || env.MINECUT_PYTHON;
        var venv = path.join(root, ".venv", os.platform() === "win32" ? "Scripts/python.exe" : "bin/python");
        if (!python && fs.existsSync(venv)) python = venv;
        if (!python) throw Error("Runtime missing. Install the full Windows package or run setup-source for this computer.");
        return {
            exe: python,
            args: [ "-u", path.join(root, "backend", "worker.py"), "--root", root ]
        };
    }
    function invoke(request, onProgress, tracked) {
        return new Promise(function(resolve, reject) {
            if (!nodeRequire) return reject(Error("Native runtime unavailable. Open Minecut in Premiere Pro."));
            var command;
            try {
                command = runtimeCommand();
            } catch (e) {
                return reject(e);
            }
            var proc = cp.spawn(command.exe, command.args, {
                cwd: root,
                windowsHide: true,
                detached: os.platform() !== "win32",
                stdio: [ "pipe", "pipe", "pipe" ],
                env: Object.assign({}, nodeRequire("process").env, {
                    PYTHONUTF8: "1",
                    OMP_NUM_THREADS: "4",
                    OPENBLAS_NUM_THREADS: "1"
                })
            });
            if (tracked) active = proc;
            var pending = "", stderr = "", answer = null, failure = null;
            proc.stdout.setEncoding("utf8");
            proc.stderr.setEncoding("utf8");
            proc.stdout.on("data", function(s) {
                pending += s;
                var i;
                while ((i = pending.indexOf("\n")) >= 0) {
                    var line = pending.slice(0, i);
                    pending = pending.slice(i + 1);
                    try {
                        var data = JSON.parse(line);
                        if (data.type === "progress" && onProgress) onProgress(data); else if (data.type === "result") answer = data; else if (data.type === "error") failure = data.message;
                    } catch (e) {}
                }
            });
            proc.stderr.on("data", function(s) {
                stderr = (stderr + s).slice(-5e3);
            });
            proc.on("error", function(e) {
                reject(Error("Cannot start Minecut runtime: " + e.message));
            });
            proc.on("close", function(code) {
                if (tracked) active = null;
                if (cancelled && tracked) return reject(Error("Analysis cancelled. Completed cached work is kept."));
                if (code !== 0 || failure || !answer) return reject(Error(failure || stderr || "Worker exited without a result (" + code + ")."));
                resolve(answer);
            });
            proc.stdin.on("error", function() {});
            proc.stdin.end(JSON.stringify(Object.assign({
                work: work
            }, request)) + "\n");
        });
    }
    function lock(value) {
        busy = value;
        $("run").disabled = value;
        [ "browse", "selection", "import" ].forEach(function(id) {
            $(id).disabled = value;
        });
        document.querySelectorAll(".presets button,#actions button,.chip,.section input,.section select,.advanced input,.advanced select,.advanced button,#allCategories").forEach(function(el) {
            el.disabled = value;
        });
    }
    function clock(seconds) {
        return Math.floor(seconds / 60) + ":" + String(Math.floor(seconds % 60)).padStart(2, "0");
    }
    function run(resolved) {
        clearError();
        if ((!source || sourceMode === "timeline") && resolved !== true) {
            lock(true);
            host("selectedSource").then(function(r) {
                setSource(r);
                lock(false);
                run(true);
            }).catch(function(e) {
                lock(false);
                showError(e);
            });
            return;
        }
        var opts = settings();
        if (Array.from(document.querySelectorAll("input[type=number]")).some(function(input) {
            return !input.checkValidity();
        })) return showError(Error("Check numeric settings: use values within the displayed limits and whole frames."));
        if (!opts.categories.length) return showError(Error("Select at least one block category."));
        if (!Number.isInteger(opts.every) || opts.every < 1 || opts.every > 1e4) return showError(Error("Keep every must be a whole number between 1 and 10000."));
        if (opts.max_audio && (!Number.isInteger(opts.peak_before) || !Number.isInteger(opts.peak_after) || opts.peak_before < 0 || opts.peak_after < 0 || opts.peak_before + opts.peak_after < 1)) return showError(Error("Choose valid frame handles for Max audio."));
        if (!Number.isInteger(opts.offset) || opts.offset < 0 || !Number.isInteger(opts.max_clips) || opts.max_clips < 0 || !Number.isFinite(opts.min_gap) || opts.min_gap < 0) return showError(Error("Choose valid selection and pacing settings."));
        if (!Number.isFinite(opts.before) || !Number.isFinite(opts.after) || opts.before < 0 || opts.after < 0 || opts.before + opts.after <= 0) return showError(Error("Choose valid padding before and after the event."));
        lock(true);
        cancelled = false;
        started = Date.now();
        $("progress").classList.remove("hidden");
        $("result").classList.add("hidden");
        $("stage").textContent = cached ? "Rebuilding cut..." : "Starting local runtime...";
        $("progressBar").value = 0;
        var etaUntil = null;
        $("elapsed").textContent = "0:00 elapsed";
        $("estimate").textContent = "Estimating...";
        $("percent").textContent = "0%";
        timer = setInterval(function() {
            $("elapsed").textContent = clock((Date.now() - started) / 1e3) + " elapsed";
            if (etaUntil !== null) {
                var left = (etaUntil - Date.now()) / 1e3;
                $("estimate").textContent = left > 0 ? "~" + clock(Math.ceil(left)) : "Updating estimate...";
            }
        }, 500);
        var req = {
            command: cached ? "build" : "analyze",
            source: source,
            source_range: sourceRange,
            events_path: cached,
            options: opts,
            device: $("device").value,
            batch: Number($("batch").value)
        };
        invoke(req, function(p) {
            if (typeof p.eta_total_seconds === "number" && Number.isFinite(p.eta_total_seconds) && p.eta_total_seconds >= 0) etaUntil = Date.now() + p.eta_total_seconds * 1e3;
            $("stage").textContent = sentencePunctuation(p.stage);
            $("progressBar").value = Math.max($("progressBar").value, p.fraction || 0);
            $("percent").textContent = Math.round($("progressBar").value * 100) + "%";
        }, true).then(function(r) {
            result = r;
            cached = r.events_path;
            $("clipCount").textContent = r.summary.clips;
            $("duration").textContent = clock(r.summary.seconds);
            $("removed").textContent = r.summary.duplicates_removed;
            $("resultNotes").textContent = (r.summary.audio_aligned ? r.summary.audio_aligned + " windows aligned to audio. " : "") + (r.summary.quiet_windows ? r.summary.quiet_windows + " quiet windows (" + (opts.quiet_policy === "skip" ? "skipped" : "original timing kept") + "). " : "") + (r.summary.spacing_removed ? r.summary.spacing_removed + " skipped for spacing." : "");
            $("result").classList.remove("hidden");
            $("importStatus").textContent = "";
            $("import").disabled = !r.xml_path;
            $("runLabel").textContent = "Rebuild cut";
            $("cacheHint").textContent = "Change categories, timing or sampling without rerunning the model.";
            if (!r.xml_path) showError(Error(r.message));
        }).catch(showError).then(function() {
            clearInterval(timer);
            lock(false);
            if (result && !result.xml_path) $("import").disabled = true;
            $("progress").classList.add("hidden");
        });
    }
    function cancel() {
        if (!active) return;
        cancelled = true;
        $("stage").textContent = "Cancelling...";
        if (os.platform() === "win32") cp.spawn("taskkill.exe", [ "/PID", String(active.pid), "/T", "/F" ], {
            windowsHide: true
        }); else try {
            nodeRequire("process").kill(-active.pid, "SIGTERM");
        } catch (e) {
            active.kill();
        }
    }
    function importEdit() {
        if (!result || !result.xml_path) return;
        clearError();
        $("import").disabled = true;
        host("projectInfo").then(function(p) {
            if (!p.path) throw Error("Save your Premiere project first, then import.");
            return host("importEdit", {
                path: result.xml_path,
                projectPath: p.path,
                clips: result.summary.clips,
                openAfter: $("openAfter").checked
            });
        }).then(function(r) {
            $("importStatus").textContent = sentencePunctuation("Created and saved: " + r.name);
        }).catch(function(e) {
            showError(e);
            $("importStatus").textContent = "Inspect Premiere before retrying if an import partially completed.";
        });
    }
    cats.forEach(function(c, i) {
        var b = document.createElement("button");
        b.className = "chip";
        b.dataset.value = c;
        b.setAttribute("aria-pressed", "true");
        var img = document.createElement("img");
        img.src = "assets/" + c + ".png";
        img.alt = "";
        b.appendChild(img);
        b.appendChild(document.createTextNode(labels[i]));
        b.title = c === "constructed" ? "Constructed/building blocks" : labels[i];
        b.onclick = function() {
            b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true"));
            markChanged();
        };
        $("categories").appendChild(b);
    });
    document.querySelectorAll("#actions button").forEach(function(b) {
        b.onclick = function() {
            setAction(b.dataset.value);
            markChanged();
        };
    });
    document.querySelectorAll("[data-preset]").forEach(function(b) {
        b.onclick = function() {
            apply({
                action: b.dataset.preset === "building" ? "place" : "break",
                categories: b.dataset.preset === "ores" ? [ "ore" ] : cats,
                unit: "frames",
                before: 3,
                after: 6,
                every: 1,
                duplicates: "one"
            });
        };
    });
    $("allCategories").onclick = function() {
        document.querySelectorAll(".chip").forEach(function(b) {
            b.setAttribute("aria-pressed", "true");
        });
        markChanged();
    };
    $("browse").onclick = function() {
        host("browse").then(setSource).catch(showError);
    };
    $("selection").onclick = function() {
        host("selectedSource").then(setSource).catch(showError);
    };
    [ "before", "after", "every", "duplicates", "sequenceName", "startEvent", "maxClips", "maxSeconds", "limitMode", "duplicateWinner", "minGap", "cutOrder", "maxAudio", "peakBefore", "peakAfter", "peakStream", "peakChannel", "peakFloor", "quietPolicy", "peakClamp" ].forEach(function(id) {
        $(id).addEventListener("input", function() {
            advancedUI();
            markChanged();
        });
    });
    $("unit").onchange = function() {
        unitUI();
        $("before").value = $("unit").value === "seconds" ? ".05" : "3";
        $("after").value = $("unit").value === "seconds" ? ".1" : "6";
        markChanged();
    };
    $("run").onclick = run;
    $("cancel").onclick = cancel;
    $("import").onclick = importEdit;
    $("savePreset").onclick = function() {
        try {
            localStorage.setItem("minecut-preset-v1", JSON.stringify(settings()));
            $("cacheHint").textContent = "Your settings are saved on this computer.";
        } catch (e) {
            showError(e);
        }
    };
    $("loadPreset").onclick = function() {
        try {
            var s = localStorage.getItem("minecut-preset-v1");
            if (!s) throw Error("No saved settings yet.");
            apply(JSON.parse(s));
        } catch (e) {
            showError(e);
        }
    };
    $("pythonPath").value = localStorage.getItem("minecut-python") || "";
    $("pythonPath").onchange = function() {
        localStorage.setItem("minecut-python", $("pythonPath").value);
        diagnose();
    };
    $("openWork").onclick = function() {
        if (!nodeRequire) return;
        fs.mkdirSync(work, {
            recursive: true
        });
        cp.spawn(os.platform() === "win32" ? "explorer.exe" : "open", [ work ], {
            windowsHide: true
        });
    };
    function diagnose() {
        invoke({
            command: "diagnose"
        }, null, false).then(function(r) {
            $("runtime").textContent = r.cuda ? r.gpu : "CPU";
            if (!r.model_exists) showError(Error("Model missing. Reinstall the complete Minecut package."));
            if (!r.ffmpeg) showError(Error("FFmpeg missing. Reinstall the complete Minecut package."));
        }).catch(function(e) {
            $("runtime").textContent = sentencePunctuation(e.message);
        });
    }
    window.addEventListener("beforeunload", function() {
        if (active) cancel();
    });
    setInterval(refreshSelection, 1500);
    window.addEventListener("focus", refreshSelection);
    diagnose();
})();