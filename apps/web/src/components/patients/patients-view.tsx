"use client";

/**
 * Ties the patients screen together: filters, table, pagination, and the detail drawer.
 *
 * The data itself is fetched by the server component that renders this, so the first paint has
 * real rows in it. This layer owns only the interactive parts — which control is pressed, and
 * which patient is open.
 *
 * The drawer is driven by `?patient=…` in the URL rather than by state held here, so:
 * - the dashboard can link straight to a patient,
 * - closing it is a back-button away,
 * - and the Playwright suite can open Sarah Johnson by navigating rather than by clicking.
 */

import { useRouter, useSearchParams } from "next/navigation";

import { Pagination } from "@/components/patients/pagination";
import { PatientDrawer } from "@/components/patients/patient-drawer";
import { PatientFilters } from "@/components/patients/patient-filters";
import { PatientsTable } from "@/components/patients/patients-table";
import type { PatientListResponse } from "@/lib/patients";

export function PatientsView({
  data,
  today,
}: {
  data: PatientListResponse;
  today: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const openPatientId = searchParams.get("patient");
  const hasFilters =
    searchParams.getAll("status").length > 0 || (searchParams.get("search") ?? "").length > 0;

  function closeDrawer() {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("patient");
    const query = params.toString();
    // `scroll: false` so closing the drawer leaves the list where it was rather than jumping to
    // the top — which matters when someone is a hundred rows down.
    router.push(query ? `/patients?${query}` : "/patients", { scroll: false });
  }

  return (
    <>
      <PatientFilters total={data.total} />

      <PatientsTable patients={data.patients} today={today} hasFilters={hasFilters} />

      <Pagination
        page={data.page}
        totalPages={data.total_pages}
        total={data.total}
        pageSize={data.page_size}
      />

      {openPatientId ? (
        <PatientDrawer
          // Keyed by patient id so switching directly from one patient to another remounts the
          // drawer. Without the key, React would reuse the component and briefly show the
          // previous patient's details under the new patient's name.
          key={openPatientId}
          publicId={openPatientId}
          today={today}
          onClose={closeDrawer}
        />
      ) : null}
    </>
  );
}
