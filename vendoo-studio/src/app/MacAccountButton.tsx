import { Button } from "../components/ui/button";
import { HOSTED_ORIGIN, useMacApp } from "../lib/macApp";

/** In the Mac app, opens the seller's account: the same listings as the web and iPhone apps. */
export function MacAccountButton() {
  const mac = useMacApp();
  if (!mac) return null;
  return (
    <Button
      type="button"
      size="xs"
      variant="outline"
      title="See the listings in your account, the same as on the web and iPhone"
      onClick={() => window.location.assign(HOSTED_ORIGIN)}
    >
      Sign in
    </Button>
  );
}
