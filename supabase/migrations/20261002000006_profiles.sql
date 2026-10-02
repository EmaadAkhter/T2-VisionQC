-- Product profiles: per product/camera view onboarding artifacts.
-- Note: `profiles` already exists for user profiles; this is a separate table.

create table if not exists public.product_profiles (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.orgs (id) on delete cascade,
    product_id uuid not null references public.products (id) on delete cascade,
    camera_id uuid references public.cameras (id) on delete set null,
    name text not null,
    status text not null default 'draft'
        check (status in ('draft', 'active')),
    metrics jsonb not null default '{}'::jsonb,
    model_version text,
    created_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now()
);

alter table public.product_profiles enable row level security;

create policy product_profiles_select_member on public.product_profiles
    for select using (public.is_org_member(org_id));

create policy product_profiles_write_quality on public.product_profiles
    for all using (
        public.has_org_role(org_id, array['owner', 'admin', 'quality_manager'])
    )
    with check (
        public.has_org_role(org_id, array['owner', 'admin', 'quality_manager'])
    );

grant select, insert, update, delete on public.product_profiles to authenticated;
grant all on public.product_profiles to service_role;

create index if not exists product_profiles_org_idx
    on public.product_profiles (org_id, product_id);

-- Active profile pointer per product (edge settings mirror).
alter table public.settings
    add column if not exists active_profile_id text;
