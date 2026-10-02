-- Explicit privileges for API roles.
-- RLS filters rows; grants decide whether the role may touch the object at all.
-- Supabase grants table privileges by default, but views and RPCs are made
-- explicit here so a fresh local stack behaves identically to hosted.

grant usage on schema public to anon, authenticated, service_role;

-- RLS policies decide which rows are visible/mutable; these grants only make
-- the objects reachable for the authenticated API role.
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage, select on all sequences in schema public to authenticated;

-- service_role is the trusted server-side role (RLS bypassed by design).
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant all on all functions in schema public to service_role;

grant select on public.my_orgs to authenticated;

grant execute on function public.create_org(text) to authenticated;
grant execute on function public.accept_invitation(uuid) to authenticated;
grant execute on function public.is_org_member(uuid) to authenticated;
grant execute on function public.org_role(uuid) to authenticated;
grant execute on function public.has_org_role(uuid, text[]) to authenticated;
