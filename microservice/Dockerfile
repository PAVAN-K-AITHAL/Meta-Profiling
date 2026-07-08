# ── Stage 1: Build ──
# Start from the official Go image with Alpine Linux (small footprint)
FROM golang:1.23-alpine AS builder

# Set working directory inside the container
WORKDIR /app

# Copy dependency files first (Docker layer caching optimization)
# If go.mod/go.sum haven't changed, Docker reuses the cached layer
COPY go.mod go.sum ./
RUN go mod download

# Copy all source code
COPY . .

# Build the binary
# CGO_ENABLED=0 = don't link against C libraries (makes a fully static binary)
# -o /api = output file name
RUN CGO_ENABLED=0 go build -o /api .

# ── Stage 2: Run ──
# Use a tiny image (just the binary, no Go compiler)
FROM alpine:3.19

# Install CA certificates (needed for HTTPS connections)
RUN apk --no-cache add ca-certificates wget

# Copy only the compiled binary from the build stage
COPY --from=builder /api /api

# Expose port 8080
EXPOSE 8080

# Run the binary
CMD ["/api"]
