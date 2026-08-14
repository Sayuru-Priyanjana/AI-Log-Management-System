#!/bin/sh
echo "$PAT" | helm registry login ghcr.io -u Sayuru-Priyanjana --password-stdin
helm push /chart/logintel-agent-0.1.3.tgz oci://ghcr.io/sayuru-priyanjana
