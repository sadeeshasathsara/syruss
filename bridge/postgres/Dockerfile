# Postgres 16 with the two extensions the platform uses:
#   pgvector  similarity search over memory and knowledge (embeddings)
#   Apache AGE  a property graph inside Postgres (people, sites, documents and how they relate),
#               queried with openCypher, in the same transactions as everything else
# Same base as before (16-alpine), so an existing data volume keeps working unchanged.
FROM postgres:16-alpine

ARG PGVECTOR_VERSION=v0.8.7
ARG AGE_VERSION=PG16/v1.6.0-rc0

# with_llvm=no skips JIT bitcode: the base image was built with a clang that isn't installable
# here, and neither extension needs JIT.
RUN apk add --no-cache --virtual .build-deps build-base git flex bison perl \
 && git clone --depth 1 --branch "${PGVECTOR_VERSION}" https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && make -C /tmp/pgvector OPTFLAGS="" with_llvm=no \
 && make -C /tmp/pgvector install with_llvm=no \
 && git clone --depth 1 --branch "${AGE_VERSION}" https://github.com/apache/age.git /tmp/age \
 && make -C /tmp/age with_llvm=no \
 && make -C /tmp/age install with_llvm=no \
 && rm -rf /tmp/pgvector /tmp/age \
 && apk del .build-deps

# AGE is loaded for every session, so graph queries need no per-connection setup.
CMD ["postgres", "-c", "shared_preload_libraries=age"]
