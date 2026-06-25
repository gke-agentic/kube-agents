# Production GKE Infrastructure

## Overview
Infrastructure setup for us-west1 regional GKE cluster with resilient components.

## Components
- **Cluster**: GKE Standard Regional (us-west1), autoscaling enabled.
- **Redis**: HA Redis (Bitnami).
- **Nginx**: Resilient webserver with HPA.
- **Postgres**: PostgreSQL (Bitnami).
- **fastAPI**: Stateless application with HPA.

## Setup
All components deployed via Helm.
