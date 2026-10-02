-- VisionQC core schema v1.
-- Every tenant-owned table carries org_id; access is governed by RLS in
-- 20261002000002_rls.sql. Roles: owner, admin, quality_manager, operator,
-- analyst, technician.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Organizations and membership
-- ---------------------------------------------------------------------------

create table public.orgs (
    id uuid primary key default gen_random_uuid(),
    name text not null check (length(trim(name)) between 2 and 120),
    slug text not null unique,
    created_by uuid not null references auth.users (id),
    created_at timestamptz not null default now()
);

create table public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    email text not null,
    full_name text not null default '',
    created_at timestamptz not null default now()
);

create table public.memberships (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    user_id uuid not null references auth.users (id) on delete cascade,
    role text not null check (role in (
        'owner', 'admin', 'quality_manager', 'operator', 'analyst', 'technician'
    )),
    created_at timestamptz not null default now(),
    unique (org_id, user_id)
);

create table public.invitations (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    email text not null,
    role text not null check (role in (
        'admin', 'quality_manager', 'operator', 'analyst', 'technician'
    )),
    token uuid not null unique default gen_random_uuid(),
    invited_by uuid not null references auth.users (id),
    expires_at timestamptz not null default (now() + interval '72 hours'),
    accepted_at timestamptz,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Factory hierarchy and product profiles
-- ---------------------------------------------------------------------------

create table public.sites (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    name text not null check (length(trim(name)) between 1 and 120),
    created_at timestamptz not null default now()
);

create table public.lines (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    site_id uuid not null references public.sites (id) on delete cascade,
    name text not null check (length(trim(name)) between 1 and 120),
    created_at timestamptz not null default now()
);

create table public.products (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    name text not null check (length(trim(name)) between 1 and 120),
    created_at timestamptz not null default now()
);

create table public.cameras (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    line_id uuid references public.lines (id) on delete set null,
    product_id uuid references public.products (id) on delete set null,
    name text not null check (length(trim(name)) between 1 and 120),
    kind text not null check (kind in ('usb', 'rtsp', 'mobile')),
    address text not null default '',
    config jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table public.models (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    product_id uuid not null references public.products (id) on delete cascade,
    version text not null,
    n_images integer not null default 0,
    ref_score double precision,
    backbone text not null default 'WideResNet-50',
    metrics jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (product_id, version)
);

create table public.settings (
    org_id uuid not null references public.orgs (id) on delete cascade,
    product_id uuid not null references public.products (id) on delete cascade,
    threshold double precision not null default 0.46
        check (threshold >= 0 and threshold <= 1),
    delta double precision not null default 0.05
        check (delta >= 0 and delta <= 0.2),
    active_model_version text,
    updated_at timestamptz not null default now(),
    primary key (org_id, product_id)
);

-- ---------------------------------------------------------------------------
-- Inspections and audit
-- ---------------------------------------------------------------------------

create table public.inspections (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    site_id uuid references public.sites (id) on delete set null,
    line_id uuid references public.lines (id) on delete set null,
    camera_id uuid references public.cameras (id) on delete set null,
    product_id uuid references public.products (id) on delete set null,
    uid text not null,
    model_version text,
    timestamp timestamptz not null default now(),
    raw_score double precision,
    score double precision,
    threshold double precision,
    delta double precision,
    verdict text check (verdict in ('PASS', 'REVIEW', 'FAIL')),
    certainty text check (certainty in ('High', 'Medium', 'Low')),
    disposition text not null default 'PENDING'
        check (disposition in ('PASS', 'FAIL', 'PENDING')),
    disposition_by_override boolean not null default false,
    operator_note text,
    explanation text,
    region_label text,
    area_pct double precision,
    setup_status text check (setup_status in ('OK', 'Caution', 'Poor')),
    latency_ms integer,
    image_path text,
    overlay_path text,
    source text not null default 'edge'
        check (source in ('edge', 'mobile', 'demo')),
    created_at timestamptz not null default now(),
    unique (org_id, uid)
);

create table public.audit_log (
    id bigint generated always as identity primary key,
    org_id uuid not null references public.orgs (id) on delete cascade,
    actor uuid references auth.users (id) on delete set null,
    action text not null,
    target text,
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index inspections_org_time_idx on public.inspections (org_id, timestamp desc);
create index inspections_org_verdict_idx on public.inspections (org_id, verdict);
create index inspections_org_camera_idx on public.inspections (org_id, camera_id);
create index memberships_user_idx on public.memberships (user_id);
create index invitations_org_idx on public.invitations (org_id);

-- ---------------------------------------------------------------------------
-- Auth triggers and RPCs
-- ---------------------------------------------------------------------------

-- Create a profile row for every new auth user.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, full_name)
    values (
        new.id,
        coalesce(new.email, ''),
        coalesce(new.raw_user_meta_data ->> 'full_name', '')
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- Create an organization and make the caller its owner.
create or replace function public.create_org(org_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    new_id uuid;
    base_slug text;
    final_slug text;
begin
    if auth.uid() is null then
        raise exception 'Not authenticated';
    end if;
    if length(trim(org_name)) < 2 then
        raise exception 'Organization name is too short';
    end if;

    base_slug := lower(regexp_replace(trim(org_name), '[^a-zA-Z0-9]+', '-', 'g'));
    base_slug := trim(both '-' from base_slug);
    if base_slug = '' then
        base_slug := 'org';
    end if;
    final_slug := base_slug || '-' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 6);

    insert into public.orgs (name, slug, created_by)
    values (trim(org_name), final_slug, auth.uid())
    returning id into new_id;

    insert into public.memberships (org_id, user_id, role)
    values (new_id, auth.uid(), 'owner');

    insert into public.audit_log (org_id, actor, action, target)
    values (new_id, auth.uid(), 'org.created', new_id::text);

    return new_id;
end;
$$;

-- Accept an invitation addressed to the signed-in user's email.
create or replace function public.accept_invitation(invite_token uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    invite record;
    caller_email text;
begin
    if auth.uid() is null then
        raise exception 'Not authenticated';
    end if;

    select email into caller_email from auth.users where id = auth.uid();

    select * into invite
    from public.invitations
    where token = invite_token
      and accepted_at is null
      and expires_at > now()
      and lower(email) = lower(caller_email)
    for update;

    if not found then
        raise exception 'Invitation not found, expired, or not addressed to this account';
    end if;

    insert into public.memberships (org_id, user_id, role)
    values (invite.org_id, auth.uid(), invite.role)
    on conflict (org_id, user_id) do update set role = excluded.role;

    update public.invitations set accepted_at = now() where id = invite.id;

    insert into public.audit_log (org_id, actor, action, target)
    values (invite.org_id, auth.uid(), 'invitation.accepted', invite.id::text);

    return invite.org_id;
end;
$$;

-- Convenience view: orgs visible to the current user with their role.
create or replace view public.my_orgs
with (security_invoker = true)
as
select o.id, o.name, o.slug, m.role
from public.orgs o
join public.memberships m on m.org_id = o.id
where m.user_id = auth.uid();
