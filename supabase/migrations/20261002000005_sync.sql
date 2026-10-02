-- Sync support: station attribution, revision tracking and station registry.

alter table public.inspections
    add column if not exists station_id text;
alter table public.inspections
    add column if not exists revision integer not null default 0;
alter table public.inspections
    add column if not exists updated_at timestamptz not null default now();

-- Bump revision/updated_at on every update so edge stations can detect
-- server-side changes and re-push after local edits.
create or replace function public.bump_inspection_revision()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    new.revision := coalesce(old.revision, 0) + 1;
    return new;
end;
$$;

drop trigger if exists inspections_bump_revision on public.inspections;
create trigger inspections_bump_revision
    before update on public.inspections
    for each row execute function public.bump_inspection_revision();

-- Edge station registry: one row per installed desktop per organization.
create table if not exists public.edge_stations (
    org_id uuid not null references public.orgs (id) on delete cascade,
    id text not null,
    name text not null default '',
    app_version text,
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    primary key (org_id, id)
);

alter table public.edge_stations enable row level security;

create policy stations_select_member on public.edge_stations
    for select using (public.is_org_member(org_id));

create policy stations_insert_operator on public.edge_stations
    for insert with check (
        public.has_org_role(
            org_id,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    );

create policy stations_update_operator on public.edge_stations
    for update using (
        public.has_org_role(
            org_id,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    )
    with check (
        public.has_org_role(
            org_id,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    );

grant select, insert, update, delete on public.edge_stations to authenticated;
grant all on public.edge_stations to service_role;

create index if not exists inspections_station_idx
    on public.inspections (org_id, station_id);
