import sys

if __name__ == "__main__":
    if "--mcp" in sys.argv:
        from subadubalingo.mcp_server import main
    elif "--check-speakers" in sys.argv:
        def main():
            """Can the built exe load sherpa-onnx (voice per person)? Writes the answer to the log."""
            from subadubalingo.common import log
            try:
                from subadubalingo import speakers
                log.info("check-speakers: sherpa-onnx %s loads", speakers._load_sherpa().__version__)
            except Exception as e:
                log.exception("check-speakers: sherpa-onnx does not load: %s", e)
    elif "--install" in sys.argv:
        def main():
            """Install this copy without asking (tests; later the self-update). --connect also adds the MCP entry.
            Writes the result to the log; exit code 0 = installed, 2 = in use, 1 = failed."""
            from subadubalingo.common import log
            from subadubalingo import installer
            try:
                log.info("--install: %s", installer.install("--connect" in sys.argv))
            except PermissionError as e:
                log.warning("--install: in use: %s", e)
                sys.exit(2)
            except Exception:
                log.exception("--install failed")
                sys.exit(1)
    else:
        from subadubalingo.gui import main
    main()
