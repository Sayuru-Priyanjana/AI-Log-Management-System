#!/bin/sh
echo "$PAT" | helm registry login ghcr.io -u Sayuru-Priyanjana --password-stdin
helm push logintel-agent-0.1.4.tgz oci://ghcr.io/sayuru-priyanjana
