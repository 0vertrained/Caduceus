# syntax=docker/dockerfile:1.10
# The model inside the network: llama.cpp's server with Qwen2.5-1.5B-Instruct (Apache 2.0), on a CPU. It does the
# work that needs the patient's details (the clinician's chart summary), so it is built to have nothing to call out with:
# - built from source at a pinned commit, without its HTTPS client (LLAMA_OPENSSL=OFF), its web UI (which can
#   download models), RPC (the backend with past remote-code-execution advisories), tests or examples;
# - the weights baked in, pinned by revision and sha256, and served with --offline;
# - listening on 127.0.0.1 by default, a sidecar's own address: on Cloud Run only the API container takes traffic.
# Grype cannot see a library compiled from source, so watch https://github.com/ggml-org/llama.cpp/security/advisories
# and move the pin when one lands. CI builds this image only when this folder changes (model-image.yml).

# The same Wolfi base, at the same digest, in both stages: newer apks on an older base can fail on glibc.
FROM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73 AS build
RUN apk add --no-cache build-base cmake git
# llama.cpp v0.8.0, refused unless the tag resolves to this commit (a moved tag fails the build).
RUN git clone --depth 1 --branch v0.8.0 https://github.com/ggml-org/llama.cpp /src \
 && test "$(git -C /src rev-parse HEAD)" = 8642541b255bfb5dc39cd351a178be09013a55d6
# Every CPU variant, chosen at start for the machine it runs on; nothing reaches the network while it builds.
RUN --network=none cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
      -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_OPENMP=OFF -DGGML_RPC=OFF \
      -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
      -DCMAKE_BUILD_RPATH_USE_ORIGIN=ON \
 && cmake --build /build --target llama-server -j"$(nproc)" \
 && mkdir /out && cp -a /build/bin/llama-server /build/bin/*.so* /out/

FROM cgr.dev/chainguard/wolfi-base:latest@sha256:88a20e2d017bd20cdc0cb3910de603e7bea9512a6c351a8dd3c9df212ab03d73
RUN apk add --no-cache libstdc++ && adduser -D -H -u 10002 model
COPY --from=build /out/ /app/
# Qwen2.5-1.5B-Instruct, Q4_K_M, from Qwen's own GGUF repository at a fixed revision.
ADD --chmod=0444 --checksum=sha256:6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e \
    https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/62a8d092b0a1047016f3edbd0fde387598727aa5/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    /models/model.gguf
# Overridable per deployment: docker compose sets LLAMA_ARG_HOST=0.0.0.0, on a network with no route out.
ENV LLAMA_ARG_HOST=127.0.0.1 LLAMA_ARG_PORT=8081 LLAMA_ARG_THREADS=2
USER model
ENTRYPOINT ["/app/llama-server", "--offline", "--no-webui", "--no-slots", "-m", "/models/model.gguf", \
            "-a", "qwen2.5-1.5b-instruct", "-c", "4096", "-np", "1"]
