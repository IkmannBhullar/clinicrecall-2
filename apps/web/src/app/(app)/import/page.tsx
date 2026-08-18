/**
 * Import patients (SPEC §7).
 *
 * Reached from the Patients screen rather than from the main navigation, which stays at the five
 * items SPEC §8 names. Importing is something you do to your patient list, not a sixth section of
 * the product.
 */

import { PageHeader } from "@/components/ui/primitives";
import { ImportWizard } from "@/components/import/import-wizard";

export const metadata = { title: "Import patients" };

export default function ImportPage() {
  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Import patients"
        description="Upload a CSV export from your practice management system. Nothing is imported until you confirm."
      />
      <ImportWizard />
    </div>
  );
}
