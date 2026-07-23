# Infrastructure Requirements — City Agent Aria

**Prepared for:** Infrastructure / DevOps team
**Application:** City Agent Aria — internal SOP / runbook assistant
**Deployment model:** Single dedicated EC2 instance (self-hosted) + Amazon S3 for document storage

---

## 1. Overview

City Agent Aria is an internal, containerised web application (web UI + API + a
background document-ingestion worker + a PostgreSQL database), deployed with Docker
Compose on **one dedicated EC2 instance**. Uploaded documents and rendered page images
are stored in **Amazon S3**. The application uses **no local AI model** — all model
inference is a remote HTTPS API call to our LLM provider (OpenRouter) — so the instance
itself is lightweight and I/O-bound.

**Managed services used:** EC2, EBS, S3 only. No RDS, no ElastiCache/Redis, no load
balancer, no vector database at this stage.

---

## 2. Architecture

```
                    ┌─────────────── EC2 instance (t3.xlarge) ───────────────┐
                    │                     Docker                              │
  Users ──HTTPS──▶  │  nginx  (TLS / reverse proxy, port 443)                 │
                    │     │                                                   │
                    │     ├──▶ App container ×2   (FastAPI API + Web UI)  — stateless
                    │     │           │                                       │
                    │     └──▶ Worker container ×1 (document ingestion) — background
                    │                 │                                       │
                    │       ┌─────────┴─────────┐                             │
                    │       │  PostgreSQL 17     │  (Docker container, EBS)    │
                    │       │  = all metadata    │                             │
                    │       └─────────┬─────────┘                             │
                    └─────────────────┼──────────────────┬────────────────────┘
                          outbound HTTPS │                │ outbound HTTPS
                                         ▼                ▼
                              OpenRouter (LLM API)   Amazon S3 (documents + images)
```

All application components run on the single instance. The only outbound traffic is
HTTPS to the LLM provider and to Amazon S3.

---

## 3. Compute (EC2)

| Item | Requirement |
|---|---|
| Instance type | **t3.xlarge** (4 vCPU / 16 GB RAM) for 100–300 users |
| Upgrade path | **m5.xlarge** if 500+ users are expected on the single instance |
| Operating system | Amazon Linux 2023 **or** Ubuntu 22.04 LTS |
| Required software | **Docker** + **Docker Compose** |
| Availability (pilot) | Single instance, single-AZ (acceptable for initial rollout) |

Rationale: the workload is read-heavy with ~40% peak concurrency; because inference is
remote, CPU/RAM demand on the box is modest.

---

## 4. Storage

### 4.1 EBS (block storage — attached to the instance)
| Item | Requirement |
|---|---|
| Volume | **1 × 60 GB gp3** |
| Contents | PostgreSQL data files + local database backup files |
| Encryption | **Encryption at rest enabled** |

### 4.2 Amazon S3 (document / image storage)
| Item | Requirement |
|---|---|
| Bucket | one private bucket, e.g. `aria-documents-prod` (same region as EC2) |
| Contents | uploaded source documents + rendered page images |
| Public access | **Blocked** |
| Encryption | **Default encryption enabled** (SSE-S3 or SSE-KMS) |
| Versioning | **Recommended** (protects against accidental delete/overwrite) |
| Access method | **IAM role attached to the EC2 instance** (preferred — no stored keys) |
| IAM permissions | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` — scoped to this bucket only |
| Network | **S3 VPC gateway endpoint recommended** (keeps S3 traffic off the public internet) |

> If an IAM instance role is not possible, scoped access keys can be provided instead —
> please advise.

---

## 5. Database

- **PostgreSQL 17**, run as a Docker container **on the EC2 instance** (self-hosted;
  **no Amazon RDS**).
- Data persisted to the EBS volume.
- Stores **all structured data**: document metadata & text, **chat history
  (conversations + messages)**, knowledge/facts, users, roles, groups, analytics, and
  configuration.
- No external cache (Redis) and no vector database are required — the retrieval design
  is vectorless and self-contained.

---

## 6. Networking & Security

| Direction | Port / target | Purpose |
|---|---|---|
| Inbound | **443 (HTTPS)** | application access |
| Inbound | **80** | redirect to 443 only |
| Inbound | **22 (SSH)** | restricted to office IP / bastion only |
| Outbound | **443 → OpenRouter** | LLM inference (required) |
| Outbound | **443 → Amazon S3** | document/image storage (VPC endpoint preferred) |

- **TLS certificate + domain:** a hostname such as `aria.<ourdomain>` is required.
  Please confirm the certificate source (AWS ACM or Let's Encrypt) and DNS ownership.
- Security group: deny all except the ports above.

---

## 7. Backups

| Data | Method | Owner |
|---|---|---|
| Database | **Daily automated `pg_dump` via cron** on the instance (retention managed in-app) | Application team |
| Documents / images | **S3 durability + bucket versioning** | AWS-managed |
| Optional DR | Nightly EBS snapshot + copy of the DB dump to a second location | Infra team |

---

## 8. External Dependencies

- **OpenRouter (LLM API)** over HTTPS — the only external inference dependency.
- **Amazon S3** — document/image storage.

No other third-party services are contacted.

---

## 9. Provisioning Checklist (please confirm / provide)

1. **AWS region** (nearest our users — e.g. `ap-southeast-1`).
2. **EC2 instance** — t3.xlarge (or m5.xlarge), chosen OS, Docker + Compose installed.
3. **EBS** — 60 GB gp3, encryption at rest enabled.
4. **S3 bucket** — name, encryption, versioning, public access blocked.
5. **IAM instance role** — scoped S3 permissions on that bucket (or access keys if role not possible).
6. **S3 VPC gateway endpoint** — can it be enabled.
7. **Domain / DNS / TLS** — hostname, certificate source, DNS ownership.
8. **Environments** — production only, or a separate staging instance as well.
9. **Access** — who holds SSH/IAM access; who patches the OS.
10. **Go-live date / maintenance window.**

---

## 10. Scale-out (future — for awareness only)

Beyond ~500 users, we will move to a load-balanced, multi-instance design:
Application Load Balancer → an Auto Scaling Group of app instances → Amazon RDS
(Multi-AZ) → the **same S3 bucket**. The application is already built for this
(stateless application tier, single background worker), so the transition is a
configuration change, not redevelopment. This will be raised as a separate discussion
when we approach that scale.

---

## 11. Indicative Monthly Cost (us-east-1, on-demand — for budgeting)

| Item | Approx. |
|---|---|
| EC2 t3.xlarge | ~$120 |
| EBS 60 GB gp3 | ~$5 |
| S3 (documents, low volume) | ~$3–10 |
| Data transfer | minimal (internal usage) |
| **AWS subtotal** | **~$130–140 / month** (≈ −40% with a Reserved Instance / Savings Plan) |
| LLM usage (OpenRouter) | ~$25–50 / month at 100–300 users |

---

*Prepared by: Rahul Gupta. Please contact us with any questions before provisioning.*
