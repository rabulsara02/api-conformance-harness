# Dockerfile — packages this project into a self-contained image.
#
# Base image matches local Python (3.13) so behavior can't diverge between the
# laptop, the container, and CI.
FROM python:3.13-slim

# All later commands run from /app inside the container.
WORKDIR /app

# Copy ONLY the dependency list first, then install. Because Docker caches each
# layer, editing project code later does not invalidate this layer — so pip does
# not re-run on every rebuild. Slow-changing things early, fast-changing late.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now bring in the rest of the project (minus everything in .dockerignore).
COPY . .

# Default command if none is given at run time. Compose overrides this per service.
CMD ["pytest"]